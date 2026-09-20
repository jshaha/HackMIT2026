"""
Demo scenario override — a hardcoded 'distressed / too-fatigued patient' for the
demo: elevated heart rate, fast breathing, high fatigue (STOP), anxious emotion.

Writes vitals-dashboard/public/reading.json on a loop (with light jitter so it
reads as live) and periodically refreshes the EDC + oversight off it, so the whole
dashboard — vitals, the red too-fatigued alert, the eCRF, and the oversight panel —
reflects the scenario. Run this INSTEAD of the live sensor:

    ./monitor.sh --stop
    conda activate papagei_env
    python demo_override.py        # Ctrl-C to stop
"""
import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PUB = os.path.join(HERE, "vitals-dashboard", "public")
FS, DUR, HR_BPM = 30, 20, 118


def _wave(n, fs, bpm):
    hz, out, ph = bpm / 60.0, [], 0.0
    for i in range(n):
        t = i / fs
        ph += 2 * math.pi * hz / fs
        out.append(math.sin(ph) + 0.4 * math.sin(2 * ph + 0.9) + 0.03 * math.sin(t * 90))
    m = max(abs(x) for x in out) or 1.0
    return [round(x / m, 4) for x in out]


WAVE = _wave(DUR * FS, FS, HR_BPM)


def _reading(i):
    jit = math.sin(i * 0.7)
    hr = round(116 + 4 * jit, 1)            # ~112–120 BPM (elevated)
    br = round(28 + 2 * abs(jit), 1)        # ~28–30 /min (fast)
    score = round(0.80 + 0.03 * jit, 3)     # high fatigue
    contributions = [
        {"feature": "hrv_rmssd", "value": 18, "weight": 0.24, "contribution": 0.19,
         "note": "low HRV — autonomic strain"},
        {"feature": "face_arousal", "value": 0.84, "weight": 0.22, "contribution": 0.12,
         "note": "anxious / tense facial affect"},
        {"feature": "voice_fatigue_index", "value": 0.71, "weight": 0.15, "contribution": 0.10,
         "note": "elevated vocal fatigue"},
        {"feature": "heart_rate", "value": hr, "weight": 0.10, "contribution": 0.09,
         "note": f"elevated HR {hr} BPM"},
        {"feature": "breathing_rate", "value": br, "weight": 0.10, "contribution": 0.08,
         "note": f"fast breathing {br}/min"},
    ]
    fatigue = {
        "fatigue_score": score, "too_fatigued": True,
        "trial_recommendation": "stop", "next_action": "halt", "confidence": 0.87,
        "reasoning": ("Elevated heart rate and breathing, reduced HRV and anxious affect — "
                      "the patient is too fatigued / distressed to safely continue; "
                      "recommend stopping the session."),
        "contributions": contributions,
        "weights_rationale": "Research-weighted across HRV, facial affect, vocal and cardiac markers.",
        "engine": "demo-scenario",
        "inputs": {"heart_leg": True, "voice_leg": True, "face_leg": True, "sqi": 0.92},
    }
    return {
        "source": "iPhone (Vitals AR) · live",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "duration_s": 20.0, "fs": FS, "hr": hr, "br": br, "sqi": 0.92,
        "hrv": {"sdnn": 27, "rmssd": 18, "pnn50": 4.5, "lf_hf": 2.7, "breathingrate": br},
        "bvp": WAVE,
        "face_emotion": {"arousal": 0.84, "valence": 0.19,
                         "emotional_state": "anxious", "source": "fer-ferplus"},
        "voice": {"arousal_index": 0.42, "valence": 0.28, "dominance": 0.4,
                  "fatigue_index": 0.71, "emotional_state": "tense / stressed",
                  "emotion_source": "wav2vec2-msp-dim", "pause_ratio": 0.34,
                  "speech_rate_hz": 2.3, "is_patient": True, "speaker_similarity": 0.9},
        "fatigue": fatigue,
    }


def _write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def main():
    print("[demo] scenario: HR ~118, fast breathing ~29/min, high fatigue (STOP), "
          "emotion 'anxious'. Ctrl-C to stop.", flush=True)
    os.makedirs(PUB, exist_ok=True)
    i, last_edc = 0, 0.0
    while True:
        _write(os.path.join(PUB, "reading.json"), _reading(i))
        # refresh the EDC + oversight off the demo reading every ~8s
        if time.time() - last_edc >= 8:
            last_edc = time.time()
            for script in ("edc_autofill.py", "oversight.py"):
                try:
                    subprocess.run([sys.executable, os.path.join(HERE, script),
                                    "--subject", "S-001", "--visit", "V2"],
                                   cwd=HERE, capture_output=True, timeout=40)
                except Exception:
                    pass
        i += 1
        time.sleep(2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[demo] stopped.")
