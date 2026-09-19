# AR Handoff — Vitals AR (iPhone clinician overlay)

The clinician's view of the pipeline, as AR on an iPhone. The rear camera tracks the patient's face; glass
panels float in the room around their head: **vitals** (HR / BR / HRV), the **fatigue** meter with the agent's
threshold and verdict, **emotional state** (from the voice leg, explained with the patient's chart), and
**context analysis** (the agentic loop's reasoning). A left panel holds the **visit**: time left and topics to
cover, checked off by **pinching in the scene** (pinch-and-hold then move to rearrange any panel).

```
video → open-rppg ─┐
voice → SER (GPU) ─┼→ fatigue_agent → reading.json → ar_bridge.py ──ws──▶ Vitals AR (iPhone)
db.py doctor notes ┘                                  (+ chart context, visit agenda)
```

## Run it

```bash
./setup_envs.sh          # once: creates 'rppg' + 'papagei_env' conda envs
./monitor.sh             # video + voice monitors + AR bridge; prints ws://<this Mac>:8765
./monitor.sh --stop
```
In the app: ⚙︎ → **Live backend** → `ws://<this Mac's IP>:8765` → Connect. Phone and Mac on the same Wi-Fi.
With no backend the app runs a scripted **Demo** patient (also the stage fallback).

Camera/mic: macOS must allow the terminal app you launch `monitor.sh` from (System Settings → Privacy &
Security → Camera / Microphone). `CAMERA=` / `MIC=` pick devices (`ffmpeg -f avfoundation -list_devices true -i ""`).

## Performance (M1 Pro)
| Stage | Device | Cost | Real-time headroom |
|---|---|---|---|
| open-rppg FacePhys (36×36 face crops) | CPU (JAX) | 0.7 ms / frame | ~45× |
| BlazeFace face detection | CPU (ONNX) | 1.9 ms / frame | ~17× |
| wav2vec2-large SER, 8 s clip | **Apple GPU (MPS)** | 129 ms (CPU: 541 ms) | ~62× |

`voice_emotion.py` now picks MPS → CUDA → CPU automatically (`SER_DEVICE=cpu` to force). The rPPG nets are
too small to benefit from GPU dispatch, so they stay on CPU.

## Bridge: `ar_bridge.py`
Stdlib only (hand-rolled WebSocket). Watches `vitals-dashboard/public/reading.json` (written by
`monitor_video.py` every ~2 s) and pushes one JSON message per update; between updates it steps the agent
loop indicator (observe → reason → classify → report).

```bash
python ar_bridge.py [--port 8765] [--patient P001] [--reading path/to/reading.json]
```

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
