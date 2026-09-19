#!/usr/bin/env bash
# Capture a pulse (BVP) waveform from the iPhone via Continuity Camera.
#
# Prep once: Control Center -> Video Effects -> turn OFF Center Stage
#            (also Portrait / Studio Light). Prop the phone steady, face it,
#            with even lighting.
#
# Usage:  ./capture_iphone.sh [seconds] [out.npz]
#   ./capture_iphone.sh            # 30s -> iphone_bvp.npz
#   ./capture_iphone.sh 45         # 45s -> iphone_bvp.npz
#   ./capture_iphone.sh 30 me.npz  # 30s -> me.npz
set -euo pipefail

SECONDS_ARG="${1:-30}"
OUT="${2:-iphone_bvp.npz}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source /opt/miniconda3/etc/profile.d/conda.sh
conda activate rppg

# --camera 1 = iPhone (Continuity); OPENCV_AVFOUNDATION_SKIP_AUTH lets the
# capture thread bypass OpenCV's main-thread auth request on macOS.
OPENCV_AVFOUNDATION_SKIP_AUTH=1 python -u "$HERE/capture_bvp.py" \
    --seconds "$SECONDS_ARG" --camera 1 --out "$OUT" \
  2>&1 | grep -v "^objc\|spurious\|duplicate must\|cannot join current thread\|Exception in thread\|Traceback\|File \"\|self\.\|raise Runtime\|^ *\^\|with self\|threading\.py\|main\.py:"

# Compute HR/HRV and write reading.json for the dashboard.
python -u "$HERE/export_reading.py" --in "$OUT" \
    --out "$HERE/vitals-dashboard/public/reading.json"

# Print a human-readable summary of the reading in the terminal.
python - "$HERE/vitals-dashboard/public/reading.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
h = r.get("hrv", {}) or {}
def fmt(x, u=""):
    return f"{x}{u}" if x is not None else "—"
print("")
print("┌─ Reading summary " + "─" * 30)
print(f"│ Source     : {r.get('source','—')}")
print(f"│ Captured   : {r.get('captured_at','—')}")
print(f"│ Duration   : {fmt(r.get('duration_s'),'s')}  @ {fmt(r.get('fs'),' Hz')}")
print(f"│ Heart rate : {fmt(r.get('hr'),' BPM')}   (SQI {fmt(r.get('sqi'))})")
print(f"│ Breathing  : {fmt(r.get('br'),' /min')}")
print("│ HRV")
print(f"│   SDNN     : {fmt(h.get('sdnn'),' ms')}")
print(f"│   RMSSD    : {fmt(h.get('rmssd'),' ms')}")
print(f"│   pNN50    : {fmt(h.get('pnn50'),' %')}")
print(f"│   LF/HF    : {fmt(h.get('lf_hf'))}")
print(f"│   Breathing: {fmt(h.get('breathingrate'),' /min')}")
print("└" + "─" * 48)
PY

echo ""
echo "Next: conda activate papagei_env && python bvp_to_papagei.py --in $OUT"
