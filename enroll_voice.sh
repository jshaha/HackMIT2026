#!/usr/bin/env bash
# One-time patient voice enrollment. Records a few seconds of the patient
# speaking, then saves their voice print to patient_voice.npy. After this,
# the live voice monitor only analyzes this speaker (noise + other people
# talking are rejected).
#
#   ./enroll_voice.sh            # 8s from mic 1
#   ./enroll_voice.sh 10 2       # 10s from mic 2
set -euo pipefail

SECONDS_ARG="${1:-8}"
MIC="${2:-1}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo "🎤 Patient enrollment — device [${MIC}], ${SECONDS_ARG}s."
echo "   Have the PATIENT speak naturally for the whole window. Get ready..."
for n in 3 2 1; do printf "   starting in %s\r" "$n"; sleep 1; done
echo "🔴 SPEAK NOW until you see ⏹ DONE                         "

ffmpeg -y -f avfoundation -i ":${MIC}" -t "$((SECONDS_ARG + 2))" -ac 1 -ar 16000 \
    "$HERE/enroll.wav" 2>/dev/null
echo "⏹ DONE"

source /opt/miniconda3/etc/profile.d/conda.sh
conda activate papagei_env
python -u "$HERE/enroll_patient.py" --in "$HERE/enroll.wav" --out "$HERE/patient_voice.npy"
