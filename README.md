# Contactless Clinical-Trial Vitals & Fatigue Monitor

Reads a patient's **heart rate, breathing, HRV, emotional state and fatigue** from a
face video + voice — no wearable — decides whether they're **too fatigued to continue
a trial visit**, and integrates with the trial's **Schedule of Activities + EDC (Veeva
Vault CDMS-shaped)**: it suggests what still needs covering, auto-fills & signs the
eCRF from what it sees and hears, and runs **automated protocol oversight** so no
separate monitor is needed.

The iPhone (Vitals AR app) is the sensor and the AR overlay; the Mac runs the model
pipeline; a web dashboard shows everything live.

## Whole pipeline

```mermaid
flowchart TB
    subgraph CAP["1 · Capture"]
        APP["iPhone Vitals AR app<br/>ARKit face · camera · mic"]
        MAC["Mac camera + mic<br/>(fallback)"]
    end

    APP -->|"USB via iproxy · JPEG frames + 16k PCM"| MP["monitor_phone.py"]
    MAC --> MV["monitor_video.py"]

    subgraph VID["2a · Video leg (rPPG)"]
        RPPG["open-rppg / FacePhys<br/>BVP → HR · BR · HRV · SQI"]
        SMO["VitalsSmoother<br/>harmonic-safe HR/BR"]
        FER["face_emotion.py<br/>FER+ ONNX → arousal/valence/state"]
    end

    subgraph VOC["2b · Voice leg"]
        VAD["voice_isolation.py<br/>VAD + speaker enrollment (patient-only)"]
        SER["voice_emotion.py<br/>wav2vec2 SER (Apple GPU)"]
        ACO["voice_decoder.py<br/>pitch · pauses · jitter → fatigue_index"]
    end

    subgraph CNV["2c · Conversation leg"]
        WSP["Whisper (silence-gated,<br/>hallucination-filtered) → transcript"]
        MAT["agenda_match.py<br/>MiniLM cosine topic coverage"]
    end

    MP --> RPPG & FER
    MV --> RPPG & FER
    RPPG --> SMO
    MP --> AUDIO["patient audio"]
    MV --> AUDIO
    AUDIO --> VAD --> SER & ACO
    AUDIO --> WSP --> MAT

    subgraph FUS["3 · Agentic fatigue loop — fatigue_agent.py"]
        FUSE["fuse(): research-weighted score<br/>HRV · face affect · voice · HR/BR<br/>(drops noisy rPPG below SQI floor)"]
        DEC["decide(): thresholds<br/>(silent baseline fallback)"]
        CLS["llm_classify():<br/>too fatigued for the trial?"]
    end

    SMO & FER & SER & ACO --> FUSE --> DEC --> CLS
    CLS --> REC{"continue ·<br/>pause ·<br/>stop"}

    subgraph VEE["4 · Veeva / EDC — mock Vault CDMS"]
        SOA["veeva_mock.py<br/>Schedule of Activities + eCRF defs"]
        FILL["edc_autofill.py<br/>draft + auto-sign eCRF<br/>(vitals + transcript AE/ConMed extraction)"]
        OVS["oversight.py<br/>SoA compliance · deviations · CRA report"]
    end

    DEC --> FILL
    WSP --> FILL
    MAT --> OVS
    SOA --> FILL & OVS
    REC --> OVS

    subgraph OUT["5 · Outputs"]
        JSON["reading · decision · agenda · transcript<br/>edc_forms · soa · oversight · monitoring · summary (JSON)"]
        SQL["db.py → SQLite<br/>per-patient longitudinal history + baselines"]
    end

    REC --> JSON
    FILL --> JSON
    OVS --> JSON
    JSON --> SQL

    JSON --> DASH["React dashboard<br/>vitals · fatigue alert · emotion · SoA · eCRF · oversight"]
    DEC -->|"ar_bridge.py · vitals + next-step recommendations"| APP
```

## How it adheres to the original plan

The whiteboard was: **video → open rPPG** and **voice → voice decoder** produce
**biometrics (BR, HR, fatigue, emotional state)**, made **context-aware**, fed to an
**agentic loop that classifies fatigue** → **"too fatigued?"** decision. Every node is
implemented:

| Whiteboard node | Implementation |
|---|---|
| video → open RPPG | `monitor_video.py` / `monitor_phone.py` + open-rppg → HR/BR/HRV/SQI |
| voice → voice decoder | `voice_isolation.py` + `voice_emotion.py` + `voice_decoder.py` |
| biometrics: BR · HR · fatigue | rPPG vitals + `fatigue_agent.py` fatigue score |
| emotional state | `face_emotion.py` (video) + wav2vec2 SER (voice) |
| context aware | conversation transcript + agenda coverage + oversight guidance |
| agentic loop → classify fatigue | `fatigue_agent.py` fuse → decide → `llm_classify` |
| too fatigued? (yes → stop / no → loop) | `trial_recommendation`: continue / pause / stop |

Beyond the original plan (from the Regeneron conversation): **Veeva SoA/EDC
integration**, **auto-filled & signed eCRF**, and **automated clinical oversight**.

## Run it

```bash
./setup_envs.sh                      # once: conda envs + deps
CAMERA=1 ./monitor.sh --veeva --agenda   # Mac camera + full pipeline
./monitor.sh --phone --veeva --agenda    # iPhone AR app as the sensor (over USB)
./monitor.sh --stop
python demo_override.py              # hardcoded demo scenario (distressed patient)
```

Dashboard: `cd vitals-dashboard && npm run dev` → `localhost:5199` → **Go live**.
iOS app: `vitals-ar-ios/` (build/install via Xcode or `devicectl`).

> Two conda envs: `rppg` (video) and `papagei_env` (voice / agent / EDC / AR bridge).
> Secrets live in a gitignored `.env` (`OPENAI_API_KEY`); patient DB and runtime
> outputs are gitignored too. Contactless estimates — not medical grade.
