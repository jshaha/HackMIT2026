"""
Real-time voice monitor — opportunistic emotional-state / fatigue updates.

Voice fatigue only exists while you're talking, so this loops: record a short
clip, and IF it contains speech, decode it (wav2vec2 SER + acoustic markers) and
atomically rewrite voice_features.json. Silent windows are skipped, leaving the
last real reading in place (the video monitor treats stale voice as absent).

The SER model loads once and stays warm.

Run in the 'papagei_env' env:
    conda activate papagei_env
    python monitor_voice.py --mic 1
Stop with Ctrl-C (or via monitor.sh --stop).
"""
import argparse
import json
import os
import subprocess
import tempfile
import time

import numpy as np
import soundfile as sf

from voice_decoder import extract

CLIP_SECONDS = 8       # length of each rolling voice window
MIN_SPEECH_RMS = 0.01  # skip windows quieter than this (silence)


def _atomic_write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)


def _record(mic, seconds, path):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "avfoundation", "-i", f":{mic}",
         "-t", str(seconds), "-ac", "1", "-ar", "16000", path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def _has_speech(path):
    try:
        x, sr = sf.read(path)
        if x.ndim > 1:
            x = x.mean(axis=1)
        return float(np.sqrt(np.mean(x ** 2) + 1e-12)) >= MIN_SPEECH_RMS
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mic", type=int, default=1)
    ap.add_argument("--out", default="voice_features.json")
    ap.add_argument("--seconds", type=int, default=CLIP_SECONDS)
    args = ap.parse_args()

    print(f"[voice] warming SER model + listening in {args.seconds}s windows on "
          f"mic [{args.mic}]. Speak anytime. Ctrl-C to stop.")
    # warm the model so the first real decode isn't slow
    try:
        from voice_emotion import predict  # noqa: F401
    except Exception as e:
        print(f"[voice] SER import warning: {e}")

    tmp = os.path.join(tempfile.gettempdir(), "monitor_voice.wav")
    while True:
        _record(args.mic, args.seconds, tmp)
        if not _has_speech(tmp):
            print("[voice] (silence — skipped)", flush=True)
            continue
        try:
            feats = extract(tmp)
            feats["captured_at"] = time.time()
            _atomic_write(args.out, feats)
            print(f"[voice] {feats['emotional_state']:20s} "
                  f"arousal {feats['arousal_index']}  fatigue {feats['fatigue_index']}",
                  flush=True)
        except SystemExit as e:
            print(f"[voice] (skip: {e})", flush=True)
        except Exception as e:
            print(f"[voice] error: {e}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[voice] stopped.")
