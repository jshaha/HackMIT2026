"""
AR bridge — the link between this Mac's pipeline and the Vitals AR iPhone app.

The app listens on the phone (port 8765); this side connects to it, over the USB
cable by default (iproxy / usbmux, so no network or Mac camera permissions are
involved) or to the phone's Wi-Fi address. On that one WebSocket:
  phone -> Mac  binary frames: b"V" + float64 ts + JPEG   (rear-camera video)
                               b"A" + float64 ts + PCM16  (16 kHz mono mic audio)
  Mac -> phone  text: one JSON vitals update per message (see AR_HANDOFF.md)

monitor_phone.py runs the full pipeline on those streams. This file also works
standalone for the Mac-camera setup (monitor.sh): it forwards each new
reading.json to the phone.
    python ar_bridge.py                          # over USB, patient P001
    python ar_bridge.py --phone ws://<phone-ip>:8765
"""
import argparse
import asyncio
import atexit
import json
import os
import shutil
import struct
import subprocess
import time

import db
from fatigue_agent import THRESHOLD

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT_STEPS = ("observe", "reason", "classify", "report")
APP_PORT = 8765          # the app listens here on the phone
USB_LOCAL_PORT = 18765   # iproxy forwards this Mac port to APP_PORT on the phone


# ------------------------------------------------------------- translation ---
def _short_factor(f):
    """Turn a fatigue_agent key_factor into a short chip label, or None if unremarkable."""
    f = f.lower()
    if "rmssd" in f and "low" in f:
        return "Low HRV"
    if "hr " in f and "elevated" in f:
        return "Elevated HR"
    if "hr " in f and "(low)" in f:
        return "Low HR"
    if "br " in f and "abnormal" in f:
        return "Irregular breathing"
    if "arousal_index" in f and "low" in f:
        return "Low vocal energy"
    if "slow speech" in f:
        return "Slow speech"
    if "long pauses" in f:
        return "Long pauses"
    if "voice fatigue_index" in f:
        try:
            if float(f.rsplit(" ", 1)[-1]) >= 0.5:
                return "Voice fatigue"
        except ValueError:
            pass
    return None


def _emotion(voice):
    """voice_decoder output (arousal/valence in [0,1]) -> app emotion block."""
    a, v = voice.get("arousal_index"), voice.get("valence", 0.5)
    if a is None:
        return None
    # Soft distribution over the four circumplex quadrants.
    centers = {"fatigued": (0.25, 0.3), "calm": (0.3, 0.75), "engaged": (0.75, 0.75), "stressed": (0.75, 0.25)}
    w = {k: 1 / (0.05 + (a - ca) ** 2 + (v - cv) ** 2) for k, (ca, cv) in centers.items()}
    total = sum(w.values())
    state = voice.get("emotional_state") or ""
    label = state.split("/")[0].strip().capitalize() or max(w, key=w.get).capitalize()
    return {
        "label": label,
        "valence": round(v * 2 - 1, 3),
        "arousal": round(a * 2 - 1, 3),
        "probs": {k: round(x / total, 3) for k, x in w.items()},
        "context": f"From voice ({voice.get('emotion_source', 'SER')}): arousal {a:.2f}, valence {v:.2f}",
    }


def to_update(reading, notes=None):
    """Map a monitor_video.py reading (+ doctor's notes) to the app's VitalsUpdate JSON."""
    u = {}
    hrv = reading.get("hrv") or {}
    if reading.get("hr") is not None:
        u["hr"] = {"bpm": reading["hr"], "confidence": reading.get("sqi")}
    if reading.get("br") is not None:
        u["br"] = {"rpm": reading["br"]}
    if hrv.get("rmssd") is not None or hrv.get("sdnn") is not None:
        u["hrv"] = {"rmssd_ms": hrv.get("rmssd"), "sdnn_ms": hrv.get("sdnn")}
    if reading.get("sqi") is not None:
        u["signal"] = {"quality": reading["sqi"]}

    fat = reading.get("fatigue") or {}
    factors = fat.get("key_factors") or []
    if fat:
        tf = fat.get("too_fatigued")
        u["fatigue"] = {
            "score": fat.get("fatigue_score"),
            "threshold": THRESHOLD,
            "label": {True: "fatigued", False: "alert", None: "inconclusive"}[tf],
            "confidence": fat.get("confidence"),
            "drivers": list(dict.fromkeys(filter(None, map(_short_factor, factors))))[:3],
        }
        action = {"halt": "Recommend pausing", "recheck": "Re-capturing signal", "continue": "OK to continue"}
        insights = [action.get(fat.get("next_action"), "")] + [f.split(" -> ")[0] for f in factors[:1]]
        u["context"] = {"summary": fat.get("reasoning"), "insights": [i for i in insights if i]}

    voice = reading.get("voice")
    if voice:
        emo = _emotion(voice)
        if emo:
            # Context-aware: prefer a doctor's note that explains the state over the raw numbers.
            relevant = [n["note"] for n in (notes or []) if n.get("category") in ("history", "medication")]
            if relevant:
                emo["context"] = f"Chart: {relevant[-1]}"
            u["emotion"] = emo
        fresh = time.time() - float(voice.get("captured_at") or 0) < 12
        u["voice"] = {"speaking": fresh}

    # The visit agenda: doctor's notes filed as instructions / agenda items become topics to cover.
    topics = [n["note"] for n in (notes or []) if n.get("category") in ("agenda", "instruction")]
    if topics:
        u["visit"] = {"topics": topics}
    return u


# ------------------------------------------------------------- phone link ---
def patient_notes(patient):
    """The patient's doctor's notes from db.py ([] if there's no DB or patient yet)."""
    try:
        p = db.get_patient(patient)
        return db.get_doctor_notes(p["id"]) if p else []
    except Exception:
        return []


class PhoneLink:
    """WebSocket client to the Vitals AR app (which listens on the phone). Reconnects forever.

    url=None -> over USB: runs `iproxy USB_LOCAL_PORT:APP_PORT` and connects to localhost.
    """

    def __init__(self, url=None, on_video=None, on_audio=None, on_connect=None):
        self.url = url
        self.on_video, self.on_audio, self.on_connect = on_video, on_audio, on_connect
        self.ws = None
        self._iproxy = None

    def _ensure_usb_tunnel(self):
        if self._iproxy and self._iproxy.poll() is None:
            return
        if not shutil.which("iproxy"):
            raise SystemExit("iproxy not found: brew install libimobiledevice (or pass --phone ws://<phone-ip>:8765)")
        self._iproxy = subprocess.Popen(["iproxy", f"{USB_LOCAL_PORT}:{APP_PORT}"],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        atexit.register(self._iproxy.terminate)
        time.sleep(0.5)

    async def run(self):
        import websockets
        target = self.url or f"ws://127.0.0.1:{USB_LOCAL_PORT}"
        waiting_logged = False
        while True:
            if self.url is None:
                self._ensure_usb_tunnel()
            try:
                async with websockets.connect(target, max_size=None, ping_interval=10, open_timeout=3) as ws:
                    self.ws = ws
                    waiting_logged = False
                    print(f"[link] connected to the phone ({'USB' if self.url is None else target})", flush=True)
                    if self.on_connect:
                        self.on_connect()
                    async for msg in ws:
                        if isinstance(msg, (bytes, bytearray)) and len(msg) > 9:
                            kind, ts = msg[:1], struct.unpack("<d", msg[1:9])[0]
                            payload = memoryview(msg)[9:]
                            if kind == b"V" and self.on_video:
                                self.on_video(ts, payload)
                            elif kind == b"A" and self.on_audio:
                                self.on_audio(ts, payload)
            except (OSError, asyncio.TimeoutError, websockets.exceptions.WebSocketException) as e:
                if not waiting_logged:
                    print(f"[link] waiting for the Vitals AR app on {'USB' if self.url is None else target} "
                          f"(open the app, Live mode) — {type(e).__name__}", flush=True)
                    waiting_logged = True
            finally:
                if self.ws is not None:
                    print("[link] phone disconnected", flush=True)
                self.ws = None
            await asyncio.sleep(1)

    async def send(self, update):
        if self.ws is None:
            return
        try:
            await self.ws.send(json.dumps(update))
        except Exception:
            pass


async def step_agent(link, stop):
    """Walk the agent-loop indicator (observe -> reason -> classify -> report) between updates."""
    for step in AGENT_STEPS:
        if stop.is_set():
            return
        await link.send({"context": {"agent_step": step}})
        await asyncio.sleep(0.45)


async def forward_readings(link, reading_path, patient):
    """Mac-camera mode: push each new reading.json (from monitor.sh) to the phone."""
    mtime, stop = 0.0, asyncio.Event()
    while True:
        try:
            m = os.path.getmtime(reading_path)
            if m != mtime:
                mtime = m
                with open(reading_path) as f:
                    update = to_update(json.load(f), patient_notes(patient))
                update.setdefault("context", {})["agent_step"] = "report"
                await link.send(update)
                stop.set()
                stop = asyncio.Event()
                asyncio.create_task(step_agent(link, stop))
        except (FileNotFoundError, json.JSONDecodeError):
            pass  # monitor not started yet / mid-write
        await asyncio.sleep(0.5)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phone", default=None, help="ws://<phone-ip>:8765 (default: over USB)")
    ap.add_argument("--reading", default=os.path.join(HERE, "vitals-dashboard/public/reading.json"))
    ap.add_argument("--patient", default=os.environ.get("PATIENT", "P001"))
    args = ap.parse_args()

    link = PhoneLink(args.phone)
    print(f"[bridge] forwarding {args.reading} (patient {args.patient}) to the phone", flush=True)
    await asyncio.gather(link.run(), forward_readings(link, args.reading, args.patient))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[bridge] stopped.")
