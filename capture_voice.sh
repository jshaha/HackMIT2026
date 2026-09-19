#!/usr/bin/env bash
# Capture a voice clip from the Mac microphone and decode emotional-state /
# fatigue markers (pretrained wav2vec2 SER + acoustic features).
#
# Prep: quiet-ish room. Speak continuously for the whole window (read a
#       sentence, describe your day). Silence in -> nothing to decode.
#
# Usage:  ./capture_voice.sh [seconds] [mic_index] [out.wav]
#   ./capture_voice.sh              # 15s, mic 1 -> voice.wav
#   ./capture_voice.sh 20 2         # 20s from avfoundation audio device 2
#
# Find mic indices with:  ffmpeg -f avfoundation -list_devices true -i ""
set -euo pipefail

SECONDS_ARG="${1:-15}"
MIC="${2:-1}"
OUT="${3:-voice.wav}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Get-ready countdown so you know exactly when to start talking.
echo ""
echo "🎤 Voice capture — device [${MIC}], ${SECONDS_ARG}s. Get ready to speak..."
for n in 3 2 1; do printf "   starting in %s\r" "$n"; sleep 1; done

echo "🔴 LISTENING NOW — speak continuously until you see ⏹ DONE          "
# Record +2s to absorb the ~2s AVFoundation warm-up, then trim handled downstream.
ffmpeg -y -f avfoundation -i ":${MIC}" -t "$((SECONDS_ARG + 2))" -ac 1 -ar 16000 \
    "$HERE/$OUT" 2>/dev/null
echo "⏹ DONE — captured $HERE/$OUT"

source /opt/miniconda3/etc/profile.d/conda.sh
conda activate papagei_env

# Show WHEN it heard you: per-second loudness timeline (so silence/clipping is
# obvious at a glance).
python - "$HERE/$OUT" <<'PY'
import sys, numpy as np, soundfile as sf
x, sr = sf.read(sys.argv[1])
if x.ndim > 1: x = x.mean(1)
bars = " ▁▂▃▄▅▆▇█"
rmss = [float(np.sqrt(np.mean(x[i:i+sr]**2) + 1e-12)) for i in range(0, len(x), sr)]
peak = max(rmss) or 1.0
print("   heard: |" + "".join(bars[min(8, int(r/peak*8))] for r in rmss)
      + "|  (each cell = 1s; flat = silence)")
voiced = sum(1 for r in rmss if r > peak*0.15)
print(f"   {len(rmss)}s captured, ~{voiced}s with speech")
PY

python -u "$HERE/voice_decoder.py" --in "$HERE/$OUT" \
    --out "$HERE/voice_features.json"
