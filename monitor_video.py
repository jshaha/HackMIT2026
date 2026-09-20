"""
Real-time video monitor — continuous rolling HR / BR / HRV + fused fatigue.

Opens the camera ONCE and keeps it open. open-rppg accumulates the pulse signal
internally, so every UPDATE_EVERY seconds we read a trailing WINDOW-second slice,
recompute vitals, fuse with the latest voice reading, and atomically rewrite
reading.json. The dashboard polls that file, so numbers update live.

Run in the 'rppg' env:
    conda activate rppg
    OPENCV_AVFOUNDATION_SKIP_AUTH=1 python monitor_video.py --camera 1
Stop with Ctrl-C (or via monitor.sh --stop).
"""
import argparse
import json
import os
import time

import cv2
import numpy as np
import rppg

from export_reading import _breathing_rate, _num
from fatigue_agent import assess

try:
    import face_emotion
except Exception:  # never let the facial-emotion extra break the vitals loop
    face_emotion = None

FACE_EMOTION_EVERY = 0.5  # seconds between facial-expression estimates

WINDOW = 20.0       # seconds of trailing signal used per estimate
UPDATE_EVERY = 2.0  # seconds between dashboard updates
WARMUP = 8.0        # need a few seconds before HR is meaningful
MAX_BVP = 1800      # cap waveform points sent to the UI
VOICE_STALE = 25.0  # ignore a voice reading older than this (seconds)


def _atomic_write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def _write_preview(path, frame, box):
    """Write the current camera frame (with face box) as a JPEG for the UI."""
    try:
        img = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)  # open-rppg yields RGB
        if box is not None:
            (y1, y2), (x1, x2) = box[0], box[1]
            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 200, 90), 2)
        h, w = img.shape[:2]
        if w > 480:
            img = cv2.resize(img, (480, int(480 * h / w)))
        tmp = path + ".tmp.jpg"
        cv2.imwrite(tmp, img, [cv2.IMWRITE_JPEG_QUALITY, 70])
        os.replace(tmp, path)
    except Exception:
        pass


def _load_voice(path):
    """Return the voice features if present and fresh, else None."""
    if not os.path.exists(path):
        return None
    try:
        if time.time() - os.path.getmtime(path) > VOICE_STALE:
            return None
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


class VitalsSmoother:
    """Steadies the per-update HR / BR shown to clinicians and fed to the fatigue agent.

    Raw rPPG estimates jump between updates, especially at low signal quality, and often
    lock onto a harmonic of the pulse (e.g. 66 -> 137 or 66 -> 35 BPM). So each reading is:
      1. harmonic-corrected against the plausible range: sub-40 values are doubled, and a
         low-SQI value ~2x the current HR (or above 150) is halved when that lands in range;
      2. pooled over the last WINDOW_S seconds as an SQI-weighted median (noisy readings
         barely count; one outlier can't move it);
      3. eased toward that median so the number glides instead of jumping.
    The raw values are kept on the reading as hr_raw / br_raw.
    """
    WINDOW_S = 20.0
    EASE = 0.35
    HR_MIN = 42.0  # below this a resting rPPG estimate is almost surely a sub-harmonic

    def __init__(self):
        self.hr, self.br = [], []   # (t, value, weight)
        self.hr_out = self.br_out = None

    @staticmethod
    def _wmedian(samples):
        pts = sorted((v, w) for _, v, w in samples)
        half, acc = sum(w for _, w in pts) / 2, 0.0
        for v, w in pts:
            acc += w
            if acc >= half:
                return v
        return pts[-1][0]

    def _push(self, buf, t, value, weight):
        buf.append((t, value, weight))
        while buf and t - buf[0][0] > self.WINDOW_S:
            buf.pop(0)

    def update(self, reading, t=None):
        t = time.time() if t is None else t
        sqi = reading.get("sqi") or 0.0
        weight = max(0.05, sqi) ** 2
        hr = reading.get("hr")
        if hr is not None:
            reading["hr_raw"] = hr
            if hr < self.HR_MIN:
                hr *= 2  # sub-harmonic lock
            elif sqi < 0.5 and hr / 2 >= self.HR_MIN and (
                    hr > 150 or (self.hr_out and 1.75 < hr / self.hr_out < 2.25)):
                hr /= 2  # 2nd-harmonic lock
            if not (self.HR_MIN <= hr <= 180):
                hr = None
        if hr is not None:
            self._push(self.hr, t, hr, weight)
            med = self._wmedian(self.hr)
            self.hr_out = med if self.hr_out is None else self.hr_out + (med - self.hr_out) * self.EASE
            reading["hr"] = round(self.hr_out, 1)
        elif self.hr_out is not None:
            reading["hr"] = round(self.hr_out, 1)
        br = reading.get("br")
        if br is not None:
            reading["br_raw"] = br
            self._push(self.br, t, br, weight)
            med = self._wmedian(self.br)
            self.br_out = med if self.br_out is None else self.br_out + (med - self.br_out) * self.EASE
            reading["br"] = round(self.br_out, 1)
            if reading.get("hrv"):
                reading["hrv"]["breathingrate"] = reading["br"]
        return reading


def compute_reading(model, start, duration, source):
    """Vitals over the trailing window [start, now] of an open-rppg model, as a reading dict
    (the dashboard's Reading shape, minus voice/fatigue), or None if there's no usable pulse yet.
    Shared by this monitor (Mac camera) and monitor_phone.py (frames streamed from the iPhone)."""
    from datetime import datetime, timezone
    res = model.hr(start=start, end=None) or {}
    hr, sqi = res.get("hr"), res.get("SQI")
    hrv = res.get("hrv") or {}
    bvp, ts = model.bvp(start=start, end=None)
    bvp = np.asarray(bvp, dtype=float)
    if hr is None or bvp.size < model.fps * 4:
        return None

    fs = float(model.fps)
    # open-rppg reports breathingrate in Hz; convert to breaths/min.
    br = hrv.get("breathingrate")
    if br is not None and br < 2:
        br = br * 60.0
    if br is None:
        br = _breathing_rate(bvp, fs)

    # downsample waveform for the UI
    if bvp.size > MAX_BVP:
        idx = np.linspace(0, bvp.size - 1, MAX_BVP).astype(int)
        wave = bvp[idx]
    else:
        wave = bvp
    wave = (wave / (np.max(np.abs(wave)) + 1e-9)).round(4).tolist()

    return {
        "source": source,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "duration_s": round(duration, 1),
        "fs": fs,
        "hr": round(float(hr), 1),
        "br": _num(br),
        "sqi": round(float(sqi if sqi is not None else 0.0), 3),
        "hrv": {
            "sdnn": _num(hrv.get("sdnn")),
            "rmssd": _num(hrv.get("rmssd")),
            "pnn50": _num(hrv.get("pnn50")),
            "lf_hf": _num(hrv.get("LF/HF") or hrv.get("lf_hf")),
            "breathingrate": _num(br),
        },
        "bvp": wave,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--camera", type=int, default=1)
    ap.add_argument("--out", default="vitals-dashboard/public/reading.json")
    ap.add_argument("--decision-out", default="decision.json")
    ap.add_argument("--voice", default="voice_features.json")
    ap.add_argument("--source", default="iPhone (Continuity) · live")
    ap.add_argument("--llm-every", type=float, default=20.0,
                    help="seconds between optional LLM refinements (0 = never)")
    ap.add_argument("--preview", default="vitals-dashboard/public/preview.jpg",
                    help="path to write a live camera frame (empty = off)")
    ap.add_argument("--patient", default=None,
                    help="patient_code -> score fatigue against their personal baseline")
    ap.add_argument("--db", default=os.environ.get("VITALS_DB", "vitals.db"),
                    help="SQLite DB for baselines/context")
    args = ap.parse_args()

    model = rppg.Model()
    print(f"[monitor] opening camera {args.camera} — live vitals every "
          f"{UPDATE_EVERY:.0f}s over a {WINDOW:.0f}s window. Ctrl-C to stop.")

    t0 = None
    last_update = 0.0
    last_llm = 0.0
    last_preview = 0.0
    last_face_emotion = 0.0
    face_emotion_cache = None  # last {"arousal","valence","emotional_state","source"} or None
    smoother = VitalsSmoother()

    with model.video_capture(args.camera):
        for frame, box in model.preview:
            now = time.time()
            if t0 is None:
                t0 = now
            elapsed = now - t0

            # Live camera preview — written continuously (even before HR locks),
            # throttled, so you can always see what the camera sees.
            if args.preview and frame is not None and (now - last_preview) >= 0.3:
                _write_preview(args.preview, frame, box)
                last_preview = now

            # Facial-expression -> emotional-state (throttled, cached). Wrapped so
            # it can NEVER break the vitals loop; frame from open-rppg is RGB.
            if (face_emotion is not None and frame is not None and box is not None
                    and (now - last_face_emotion) >= FACE_EMOTION_EVERY):
                last_face_emotion = now
                try:
                    fe = face_emotion.predict(frame, box=box)
                    if fe is not None:
                        face_emotion_cache = {
                            "arousal": fe.get("arousal"),
                            "valence": fe.get("valence"),
                            "emotional_state": fe.get("emotional_state"),
                            "source": fe.get("source"),
                        }
                except Exception:
                    pass

            if elapsed < WARMUP or (elapsed - last_update) < UPDATE_EVERY:
                continue
            last_update = elapsed

            reading = compute_reading(model, max(0.0, elapsed - WINDOW), min(elapsed, WINDOW), args.source)
            if reading is None:
                continue
            smoother.update(reading)

            # attach voice + face emotion so all modalities are scored, then run
            # the full fatigue assessment (fuse -> decide -> optional LLM).
            voice = _load_voice(args.voice)
            reading["voice"] = voice
            reading["face_emotion"] = face_emotion_cache  # dict or None (contract)
            use_llm = bool(args.llm_every and (elapsed - last_llm) >= args.llm_every)
            verdict = assess(reading, voice, patient=args.patient,
                             db_path=args.db, use_llm=use_llm)
            if use_llm:
                last_llm = elapsed
            engine = verdict.get("engine", "rule-core")

            reading["fatigue"] = verdict
            _atomic_write(args.out, reading)
            _atomic_write(args.decision_out, verdict)

            tf = verdict.get("too_fatigued")
            tag = {True: "TOO FATIGUED", False: "ok", None: "?"}[tf]
            vstate = (voice or {}).get("emotional_state", "no voice")
            print(f"[{elapsed:5.1f}s] HR {reading['hr']:5.1f}  BR {reading['br']}  "
                  f"SQI {reading['sqi']:.2f}  fatigue {verdict.get('fatigue_score')}  "
                  f"[{tag}]  voice: {vstate}", flush=True)


if __name__ == "__main__":
    # Auto-recover: open-rppg can crash when the Continuity feed drops. Restart
    # the camera session instead of dying, so background monitoring survives.
    while True:
        try:
            main()
            break
        except KeyboardInterrupt:
            print("\n[monitor] stopped.")
            break
        except Exception as e:
            print(f"[monitor] session error: {e!r} — restarting camera in 3s",
                  flush=True)
            time.sleep(3)
