"""
Phone monitor — the full real-time pipeline on the iPhone's own camera and mic.

The Vitals AR app streams its rear-camera frames and microphone audio to this Mac
(over USB by default, see ar_bridge.py). Here:
  video -> open-rppg (same model + vitals code as monitor_video.py) -> HR / BR / HRV
  audio -> 8 s windows -> voice_decoder (wav2vec2 SER on the local GPU) -> arousal / fatigue
  both  -> fatigue_agent (rule core + optional LLM) -> verdict
and every UPDATE_EVERY seconds the fused reading goes back to the phone (plus
reading.json / decision.json / voice_features.json so the dashboard and db keep working).

No Mac camera or microphone is used, so no macOS privacy permissions are needed.

Run in 'papagei_env' (has open-rppg + torch):
    python monitor_phone.py                         # phone over USB, patient P001
    python monitor_phone.py --phone ws://<ip>:8765  # phone over Wi-Fi
"""
import argparse
import asyncio
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np
import rppg
import soundfile as sf

import ar_bridge
from fatigue_agent import decide, fuse, llm_refine
from monitor_video import UPDATE_EVERY, VOICE_STALE, WARMUP, WINDOW, VitalsSmoother, _atomic_write, compute_reading
from voice_decoder import extract

HERE = os.path.dirname(os.path.abspath(__file__))
AUDIO_SR = 16000
CLIP_SECONDS = 8       # voice window, as in monitor_voice.py
MIN_SPEECH_RMS = 0.01  # skip windows quieter than this (silence)


class PhonePipeline:
    def __init__(self, args):
        self.args = args
        self.model = None
        self.frames = 0
        self.frame_times = []
        self.audio = bytearray()
        self.voice = None
        self.voice_busy = False
        self.pool = ThreadPoolExecutor(max_workers=1)  # voice decoding off the event loop
        self.last_preview = 0.0

    def warm_up(self):
        """Pay one-time costs before streaming starts, so they never stall the live loop:
        JAX compiles the rPPG net on its first call, and the voice model loads onto the GPU."""
        t = time.time()
        self.rppg = rppg.Model()
        x = np.zeros(self.rppg.meta["input"], np.uint8)
        self.rppg.call(x, self.rppg.state)
        import voice_emotion
        voice_emotion.predict(np.zeros(AUDIO_SR, np.float32), AUDIO_SR)
        print(f"[phone] models warm in {time.time() - t:.1f}s (rPPG on CPU/JAX, voice on "
              f"{voice_emotion._device})", flush=True)

    # ---- session ----
    def start_session(self):
        """Fresh rPPG state for each phone connection (timestamps restart with the app).
        Reuses the one compiled model; entering it resets its buffers and starts its inference thread."""
        if self.model is not None:
            try:
                self.model.__exit__(None, None, None)
            except Exception:
                pass
        self.model = self.rppg
        self.model.__enter__()  # we feed frames by hand
        self.frames = 0
        self.frame_times = []
        self.audio.clear()
        self.smoother = VitalsSmoother()
        print("[phone] new session — hold the patient's face in view; vitals start after "
              f"~{WARMUP:.0f} s", flush=True)

    # ---- streams from the phone ----
    def on_video(self, ts, jpeg, is_face):
        """Face crops (the phone already tracks the face) go straight to open-rppg's face input, skipping its own
        detector/tracker, which assumes a fixed camera frame. Whole frames (no face in view) only feed the preview."""
        if self.model is None:
            return
        if is_face:
            img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                return
            self.model.update_face(cv2.cvtColor(img, cv2.COLOR_BGR2RGB), ts)  # open-rppg wants RGB
            self.frames += 1
        now = time.time()
        if is_face:
            self.frame_times = [t for t in self.frame_times if now - t < 2] + [now]
        else:
            self.frame_times = [t for t in self.frame_times if now - t < 2]
        if self.args.preview and now - self.last_preview > 0.3:  # dashboard camera preview
            self.last_preview = now
            try:
                with open(self.args.preview + ".tmp", "wb") as f:
                    f.write(jpeg)
                os.replace(self.args.preview + ".tmp", self.args.preview)
            except OSError:
                pass

    def on_audio(self, ts, pcm):
        self.audio += pcm
        n = CLIP_SECONDS * AUDIO_SR * 2
        if len(self.audio) >= n:
            clip = bytes(self.audio[:n])
            del self.audio[:n]
            if not self.voice_busy:
                self.voice_busy = True
                self.pool.submit(self._decode_voice, clip)

    def _decode_voice(self, clip):
        try:
            x = np.frombuffer(clip, dtype="<i2").astype(np.float32) / 32768.0
            if float(np.sqrt(np.mean(x ** 2) + 1e-12)) < MIN_SPEECH_RMS:
                print("[voice] (silence — skipped)", flush=True)
                return
            path = os.path.join(tempfile.gettempdir(), "monitor_phone.wav")
            sf.write(path, x, AUDIO_SR)
            feats = extract(path)
            feats["captured_at"] = time.time()
            self.voice = feats
            _atomic_write(self.args.voice, feats)
            print(f"[voice] {feats['emotional_state']:20s} arousal {feats['arousal_index']}  "
                  f"fatigue {feats['fatigue_index']}", flush=True)
        except SystemExit as e:
            print(f"[voice] (skip: {e})", flush=True)
        except Exception as e:
            print(f"[voice] error: {e!r}", flush=True)
        finally:
            self.voice_busy = False

    # ---- fusion loop ----
    async def run(self, link):
        last_llm = 0.0
        last_elapsed = -1.0
        stop_steps = asyncio.Event()
        while True:
            await asyncio.sleep(UPDATE_EVERY)
            m = self.model
            if m is None or link.ws is None:
                continue
            fps = len(self.frame_times) / 2.0
            elapsed = m.now
            if elapsed == last_elapsed:  # no new face frames since the last update
                print(f"[phone] no face in view — waiting (face video {fps:4.1f} fps)", flush=True)
                continue
            last_elapsed = elapsed
            if elapsed < WARMUP:
                print(f"[phone] warming up — {elapsed:4.1f}s of signal, face video {fps:4.1f} fps", flush=True)
                continue
            reading = compute_reading(m, max(0.0, elapsed - WINDOW), min(elapsed, WINDOW),
                                      "iPhone (Vitals AR) · live")
            if reading is None:
                print(f"[phone] no stable pulse yet (face in view?) — face video {fps:4.1f} fps", flush=True)
                continue
            self.smoother.update(reading)

            voice = self.voice if self.voice and time.time() - self.voice["captured_at"] < VOICE_STALE else None
            score, factors, sqi = fuse(reading, voice)
            verdict = decide(score, factors, sqi, True, voice is not None)
            engine = "rule-core"
            if self.args.llm_every and time.time() - last_llm >= self.args.llm_every:
                verdict, engine = await asyncio.get_running_loop().run_in_executor(
                    None, llm_refine, verdict, reading, voice)
                last_llm = time.time()
            verdict["engine"] = engine
            verdict["inputs"] = {"heart_leg": True, "voice_leg": voice is not None}
            reading["voice"] = voice
            reading["fatigue"] = verdict
            _atomic_write(self.args.out, reading)
            _atomic_write(self.args.decision_out, verdict)

            update = ar_bridge.to_update(reading, ar_bridge.patient_notes(self.args.patient))
            update.setdefault("context", {})["agent_step"] = "report"
            await link.send(update)
            stop_steps.set()
            stop_steps = asyncio.Event()
            asyncio.create_task(ar_bridge.step_agent(link, stop_steps))

            tf = {True: "TOO FATIGUED", False: "ok", None: "?"}[verdict.get("too_fatigued")]
            print(f"[{elapsed:6.1f}s] HR {reading['hr']:5.1f} (raw {reading['hr_raw']:5.1f})  BR {reading['br']}  SQI {reading['sqi']:.2f}  "
                  f"fatigue {verdict.get('fatigue_score')} [{tf}]  face video {fps:4.1f} fps  "
                  f"voice: {(voice or {}).get('emotional_state', 'none')}", flush=True)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phone", default=None, help="ws://<phone-ip>:8765 (default: over USB)")
    ap.add_argument("--patient", default=os.environ.get("PATIENT", "P001"))
    ap.add_argument("--out", default=os.path.join(HERE, "vitals-dashboard/public/reading.json"))
    ap.add_argument("--decision-out", default=os.path.join(HERE, "decision.json"))
    ap.add_argument("--voice", default=os.path.join(HERE, "voice_features.json"))
    ap.add_argument("--preview", default=os.path.join(HERE, "vitals-dashboard/public/preview.jpg"))
    ap.add_argument("--llm-every", type=float, default=20.0, help="seconds between LLM refinements (0 = never)")
    args = ap.parse_args()

    pipe = PhonePipeline(args)
    pipe.warm_up()
    link = ar_bridge.PhoneLink(args.phone, on_video=pipe.on_video, on_audio=pipe.on_audio,
                               on_connect=pipe.start_session)
    print(f"[phone] pipeline ready (patient {args.patient}); connecting to the Vitals AR app "
          f"{'over USB' if args.phone is None else 'at ' + args.phone} ...", flush=True)
    await asyncio.gather(link.run(), pipe.run(link))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[phone] stopped.")
