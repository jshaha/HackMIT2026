"""
AR bridge — streams the live pipeline to the Vitals AR iPhone app.

Watches the reading.json that monitor_video.py rewrites every ~2 s (heart leg +
fused voice leg + fatigue verdict) and pushes it over a WebSocket in the app's
contract (see AR_HANDOFF.md). Also pulls the patient's doctor's notes from
db.py so the overlay can show context and the visit's agenda.

Stdlib only (hand-rolled WebSocket server, no new deps). Run in any env:
    python ar_bridge.py                      # ws://0.0.0.0:8765, patient P001
    PATIENT=P002 python ar_bridge.py --port 9000
In the app: ⚙︎ → Live backend → ws://<this Mac's IP>:8765
"""
import argparse
import asyncio
import base64
import hashlib
import json
import os
import socket
import struct
import time

import db
from fatigue_agent import THRESHOLD

HERE = os.path.dirname(os.path.abspath(__file__))
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
AGENT_STEPS = ("observe", "reason", "classify", "report")


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


# ----------------------------------------------------------- tiny WebSocket ---
async def _handshake(reader, writer):
    request = (await reader.readuntil(b"\r\n\r\n")).decode(errors="replace")
    key = next((ln.split(":", 1)[1].strip() for ln in request.split("\r\n")
                if ln.lower().startswith("sec-websocket-key:")), None)
    if not key:
        writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
        await writer.drain()
        return False
    accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
    writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
    await writer.drain()
    return True


def _frame(text):
    data = text.encode()
    n = len(data)
    head = bytes([0x81]) + (bytes([n]) if n < 126 else
                            bytes([126]) + struct.pack(">H", n) if n < 65536 else
                            bytes([127]) + struct.pack(">Q", n))
    return head + data


async def _drain_incoming(reader):
    """Read (and discard) client frames until the client closes."""
    while True:
        hdr = await reader.readexactly(2)
        op, n = hdr[0] & 0x0F, hdr[1] & 0x7F
        if n == 126:
            n = struct.unpack(">H", await reader.readexactly(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", await reader.readexactly(8))[0]
        if hdr[1] & 0x80:
            await reader.readexactly(4)
        await reader.readexactly(n)
        if op == 0x8:
            return


class Bridge:
    def __init__(self, reading_path, patient):
        self.reading_path = reading_path
        self.patient = patient
        self.clients = set()
        self.latest = None
        self.step = 0

    def notes(self):
        try:
            p = db.get_patient(self.patient)
            return db.get_doctor_notes(p["id"]) if p else []
        except Exception:
            return []  # no DB yet: fine, just no chart context

    async def handle(self, reader, writer):
        try:
            if not await _handshake(reader, writer):
                return
            self.clients.add(writer)
            print(f"[bridge] app connected ({len(self.clients)} client(s))", flush=True)
            if self.latest:
                writer.write(_frame(json.dumps(self.latest)))
                await writer.drain()
            await _drain_incoming(reader)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self.clients.discard(writer)
            writer.close()
            print(f"[bridge] app disconnected ({len(self.clients)} client(s))", flush=True)

    async def broadcast(self, update):
        msg = _frame(json.dumps(update))
        for w in list(self.clients):
            try:
                w.write(msg)
                await w.drain()
            except ConnectionError:
                self.clients.discard(w)

    async def watch(self):
        """Push each new reading; between readings, walk the agent-loop steps so the app shows it working."""
        mtime = 0.0
        while True:
            try:
                m = os.path.getmtime(self.reading_path)
                if m != mtime:
                    mtime = m
                    with open(self.reading_path) as f:
                        reading = json.load(f)
                    self.latest = to_update(reading, self.notes())
                    self.latest.setdefault("context", {})["agent_step"] = "report"
                    self.step = 0
                    await self.broadcast(self.latest)
                elif self.latest is not None and self.step < len(AGENT_STEPS):
                    # observe -> reason -> classify -> report while the next reading is being computed,
                    # resting on "report" if the monitor pauses
                    await self.broadcast({"context": {"agent_step": AGENT_STEPS[self.step]}})
                    self.step += 1
            except (FileNotFoundError, json.JSONDecodeError):
                pass  # monitor not started yet / mid-write
            await asyncio.sleep(0.5)


def _lan_ips():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(ips) or ["127.0.0.1"]


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("AR_PORT", 8765)))
    ap.add_argument("--reading", default=os.path.join(HERE, "vitals-dashboard/public/reading.json"))
    ap.add_argument("--patient", default=os.environ.get("PATIENT", "P001"))
    args = ap.parse_args()

    bridge = Bridge(args.reading, args.patient)
    server = await asyncio.start_server(bridge.handle, "0.0.0.0", args.port)
    print(f"[bridge] streaming {args.reading} (patient {args.patient}). In the app, connect to:")
    for ip in _lan_ips():
        print(f"           ws://{ip}:{args.port}")
    async with server:
        await asyncio.gather(server.serve_forever(), bridge.watch())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[bridge] stopped.")
