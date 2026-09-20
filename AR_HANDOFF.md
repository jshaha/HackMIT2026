# AR Handoff — Vitals AR (iPhone clinician overlay)

The clinician's view of the pipeline, as AR on an iPhone. The rear camera tracks the patient's face; glass
panels float in the room around their head: **vitals** (HR / BR / HRV), the **fatigue** meter with the agent's
threshold and verdict, **emotional state** (from the voice leg, explained with the patient's chart), and
**context analysis** (the agentic loop's reasoning). A left panel holds the **visit**: time left and topics to
cover, checked off by **pinching in the scene**.

**Gestures are the Vision Pro model.** Aim at a panel (screen centre — the reticle stands in for gaze) or
simply hold your hand over it; it lights up, and a **pinch anywhere in view** activates what's highlighted.
**Pinch and hold, then move** to pick a panel up and reposition it — the grab bar on its lower edge shows when
it's ready. ⚙︎ also holds panel size (a single slider, 60–120%, 72% by default — every card is laid out at
its natural size and scaled, so the overlay resizes without anything re-flowing) and the theme: **dark glass**
(the default: charcoal cards, white text, neon teal) or **light frost**. The patient is framed by four corner
brackets rather than a traced outline, with a line running from that frame out to each metric.

The **iPhone is the sensor**: the app streams its rear-camera frames and mic audio to the Mac over the USB
cable, the Mac runs the pipeline, and the vitals come back to the phone on the same connection. No Mac camera
or microphone is used, so no macOS privacy permissions are involved, and no network is needed.

```
iPhone (Vitals AR) ── USB (iproxy) ──▶ monitor_phone.py
  camera 640×480 JPEG @30fps ─────────▶ open-rppg (FacePhys) ──▶ HR / BR / HRV ─┐
  mic 16 kHz PCM ─────────────────────▶ voice_decoder + SER (Apple GPU) ─────────┼▶ fatigue_agent
  ◀──────── vitals JSON (+ chart context, visit agenda from db.py) ──────────────┘   → reading.json too
```

## Run it

```bash
./setup_envs.sh          # once: 'rppg' + 'papagei_env' conda envs, iproxy (libimobiledevice)
./monitor.sh --phone     # models warm up ~25 s, then waits for the phone
./monitor.sh --stop
```
On the iPhone (plugged into the Mac): Vitals AR → ⚙︎ → **Live (Mac pipeline)**. The status pill turns **Live**
when the Mac connects; vitals appear ~10 s after a face is in view (0.5–1 m, good light).
`tail -f logs/monitor_phone.log` shows HR / signal quality / fps every 2 s.

- **Over Wi-Fi instead of USB:** turn on "Allow Wi-Fi connections" in the app (it shows the phone's address),
  then `python monitor_phone.py --phone ws://<phone-ip>:8765`. By default the app only accepts the USB link.
- **Mac-camera setup (original):** `./monitor.sh` runs the Mac camera/mic monitors, and `ar_bridge.py`
  forwards each `reading.json` to the phone. That path needs macOS Camera/Microphone permission.
- **No backend at all:** the app's **Demo data** mode is a scripted patient (stage fallback).

## Performance (M1 Pro)
| Stage | Device | Cost | Real-time headroom |
|---|---|---|---|
| open-rppg FacePhys (36×36 face crops) | CPU (JAX) | 0.7 ms / frame | ~45× |
| BlazeFace face detection | CPU (ONNX) | 1.9 ms / frame | ~17× |
| wav2vec2-large SER, 8 s clip | **Apple GPU (MPS)** | 129 ms (CPU: 541 ms) | ~62× |

`voice_emotion.py` now picks MPS → CUDA → CPU automatically (`SER_DEVICE=cpu` to force). The rPPG nets are
too small to benefit from GPU dispatch, so they stay on CPU.

## Link + translation: `ar_bridge.py`
The app listens on the phone (port 8765); `PhoneLink` connects to it over USB (runs `iproxy 18765:8765`) or
`--phone ws://…`, and reconnects forever. Phone → Mac binary frames: `V` + float64 ts + JPEG,
`A` + float64 ts + PCM16 16 kHz mono. Mac → phone: one JSON vitals update per message (below); between
updates it steps the agent-loop indicator (observe → reason → classify → report).
`monitor_phone.py` reuses `monitor_video.compute_reading` (same vitals code as the Mac-camera path), warms
both models before streaming so JIT/model loading never stalls the live loop, and writes `reading.json` /
`decision.json` / `voice_features.json` as before, so the dashboard and `db.py` keep working.

Mapping from the pipeline:
- `hr.bpm` ← `reading.hr` (`confidence` ← `sqi`) · `br.rpm` ← `reading.br` · `hrv` ← `reading.hrv.rmssd/sdnn`
- `fatigue` ← `reading.fatigue` (`score` = `fatigue_score`, `threshold` = `fatigue_agent.THRESHOLD`,
  `drivers` = `key_factors` shortened to chips like "Low HRV", "Slow speech")
- `context.summary` ← the agent's `reasoning`; `insights` ← `next_action` + the top factor
- `emotion` ← `reading.voice` (`arousal_index` / `valence` rescaled to −1…1, `emotional_state` label);
  its explanation comes from the latest **history/medication doctor's note** when one exists
- `visit.topics` ← doctor's notes filed with category **`agenda`** or **`instruction`**:
  ```bash
  python db.py note --patient P001 --text "Review sleep log" --category agenda
  ```

## Wire format (one JSON object per message; every field optional, snake_case)
```jsonc
{
  "hr":      { "bpm": 72.4, "confidence": 0.91 },
  "br":      { "rpm": 14.2 },
  "hrv":     { "rmssd_ms": 42.0, "sdnn_ms": 55.0 },
  "fatigue": { "score": 0.71, "threshold": 0.6, "label": "fatigued", "confidence": 0.82,
               "drivers": ["Low HRV", "Slow speech"] },
  "emotion": { "label": "Fatigued", "valence": -0.3, "arousal": -0.4,
               "probs": { "fatigued": 0.6, "calm": 0.2 }, "context": "Chart: night-shift nurse" },
  "context": { "agent_step": "reason", "summary": "…", "insights": ["Recommend pausing"] },
  "voice":   { "speaking": true, "transcript": "optional" },
  "signal":  { "quality": 0.9 },
  "visit":   { "topics": ["Review sleep log", "Discuss beta-blocker side effects"] }
}
```

## iOS app: `vitals-ar-ios/`
SwiftUI + ARKit + Vision (face landmarks for the outline/anchoring, hand pose for pinch). Project is generated
by XcodeGen from `project.yml`; set your own `DEVELOPMENT_TEAM` there.
```bash
cd vitals-ar-ios
xcodegen generate
./build.sh -destination 'id=<device udid>' -allowProvisioningUpdates build   # build.sh clears conda's LD/CC vars
xcrun devicectl device install app --device <udid> build/Build/Products/Debug-iphoneos/VitalsAR.app
```
The simulator runs a stand-in patient with a drifting virtual camera (no ARKit there).
