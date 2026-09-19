#!/usr/bin/env bash
# Real-time background monitoring: continuous video vitals + opportunistic voice.
# Writes vitals-dashboard/public/reading.json live; the dashboard polls it, and the
# AR bridge streams it to the Vitals AR iPhone app (ws://<this Mac>:8765).
#
#   ./monitor.sh            # start both monitors in the background
#   ./monitor.sh --stop     # stop them
#   ./monitor.sh --status   # show whether they're running + tail logs
#
# Logs: logs/monitor_video.log, logs/monitor_voice.log, logs/ar_bridge.log
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOGDIR="$HERE/logs"; mkdir -p "$LOGDIR"
PIDFILE="$HERE/.monitor.pids"
CAMERA="${CAMERA:-1}"
MIC="${MIC:-1}"

[ -f "$HERE/.env" ] && { set -a; source "$HERE/.env"; set +a; }

stop() {
  if [ -f "$PIDFILE" ]; then
    while read -r pid; do
      [ -n "$pid" ] && kill "$pid" 2>/dev/null && echo "stopped pid $pid" || true
    done < "$PIDFILE"
    rm -f "$PIDFILE"
  else
    echo "no pidfile — nothing tracked"
  fi
  # belt-and-suspenders
  pkill -f monitor_video.py 2>/dev/null || true
  pkill -f monitor_voice.py 2>/dev/null || true
  pkill -f ar_bridge.py 2>/dev/null || true
}

status() {
  if [ -f "$PIDFILE" ] && while read -r p; do kill -0 "$p" 2>/dev/null && exit 0; done < "$PIDFILE"; then
    echo "running (pids: $(tr '\n' ' ' < "$PIDFILE"))"
  else
    echo "not running"
  fi
  echo "--- video log (tail) ---"; tail -5 "$LOGDIR/monitor_video.log" 2>/dev/null || true
  echo "--- voice log (tail) ---"; tail -5 "$LOGDIR/monitor_voice.log" 2>/dev/null || true
  echo "--- AR bridge log (tail) ---"; tail -5 "$LOGDIR/ar_bridge.log" 2>/dev/null || true
}

case "${1:-start}" in
  --stop)   stop; exit 0 ;;
  --status) status; exit 0 ;;
esac

# fresh start
stop 2>/dev/null || true
: > "$PIDFILE"
# Find conda wherever it's installed (Miniconda, Anaconda, Homebrew); override with CONDA_SH=...
for c in "${CONDA_SH:-}" /opt/miniconda3 /opt/homebrew/anaconda3 /opt/anaconda3 "$HOME/miniconda3" "$HOME/anaconda3"; do
  [ -n "$c" ] && [ -f "${c%/etc/profile.d/conda.sh}/etc/profile.d/conda.sh" ] && { source "${c%/etc/profile.d/conda.sh}/etc/profile.d/conda.sh"; break; }
done
set +u  # conda's (de)activate hooks reference unset variables

echo "▶ starting video monitor (camera $CAMERA) ..."
( conda activate rppg
  OPENCV_AVFOUNDATION_SKIP_AUTH=1 exec python -u "$HERE/monitor_video.py" --camera "$CAMERA" \
    --out "$HERE/vitals-dashboard/public/reading.json" \
    --voice "$HERE/voice_features.json" \
) >"$LOGDIR/monitor_video.log" 2>&1 &
echo $! >> "$PIDFILE"

echo "▶ starting voice monitor (mic $MIC) ..."
( conda activate papagei_env
  exec python -u "$HERE/monitor_voice.py" --mic "$MIC" --out "$HERE/voice_features.json"
) >"$LOGDIR/monitor_voice.log" 2>&1 &
echo $! >> "$PIDFILE"

echo "▶ starting AR bridge (patient ${PATIENT:-P001}) ..."
( conda activate papagei_env
  exec python -u "$HERE/ar_bridge.py" --patient "${PATIENT:-P001}"
) >"$LOGDIR/ar_bridge.log" 2>&1 &
echo $! >> "$PIDFILE"

echo ""
echo "✅ live. PIDs: $(tr '\n' ' ' < "$PIDFILE")"
echo "   reading.json is updating ~every 2s; open the dashboard and toggle LIVE."
sleep 1; grep -m3 "ws://" "$LOGDIR/ar_bridge.log" 2>/dev/null | sed 's/^ */   iPhone app → /' || true
echo "   tail -f $LOGDIR/monitor_video.log"
echo "   stop with: ./monitor.sh --stop"
