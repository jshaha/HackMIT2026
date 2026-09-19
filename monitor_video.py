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
from fatigue_agent import fuse, decide, llm_refine

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
    args = ap.parse_args()

    model = rppg.Model()
    print(f"[monitor] opening camera {args.camera} — live vitals every "
          f"{UPDATE_EVERY:.0f}s over a {WINDOW:.0f}s window. Ctrl-C to stop.")

    t0 = None
    last_update = 0.0
    last_llm = 0.0
    last_preview = 0.0
    from datetime import datetime, timezone

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

            if elapsed < WARMUP or (elapsed - last_update) < UPDATE_EVERY:
                continue
            last_update = elapsed

            start = max(0.0, elapsed - WINDOW)
            res = model.hr(start=start, end=None) or {}
            hr, sqi = res.get("hr"), res.get("SQI")
            hrv = res.get("hrv") or {}
            bvp, ts = model.bvp(start=start, end=None)
            bvp = np.asarray(bvp, dtype=float)
            if hr is None or bvp.size < model.fps * 4:
                continue

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

            reading = {
                "source": args.source,
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "duration_s": round(min(elapsed, WINDOW), 1),
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

            # fuse with the latest fresh voice reading (may be None)
            voice = _load_voice(args.voice)
            score, factors, sqi_f = fuse(reading, voice)
            verdict = decide(score, factors, sqi_f, True, voice is not None)
            engine = "rule-core"
            if args.llm_every and (elapsed - last_llm) >= args.llm_every:
                verdict, engine = llm_refine(verdict, reading, voice)
                last_llm = elapsed
            verdict["engine"] = engine
            verdict["inputs"] = {"heart_leg": True, "voice_leg": voice is not None}

            reading["voice"] = voice
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
