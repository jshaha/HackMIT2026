#!/usr/bin/env bash
# Real-time background monitoring: continuous video vitals + opportunistic voice.
# Writes vitals-dashboard/public/reading.json live; the dashboard polls it.
#
#   ./monitor.sh --phone    # the Vitals AR iPhone app is the camera + mic (over USB);
#                           #   vitals stream back to the phone. No Mac camera/mic used.
#   ./monitor.sh            # Mac camera + mic monitors, forwarded to the phone by the AR bridge
#   ./monitor.sh --stop     # stop everything
#   ./monitor.sh --status   # show whether they're running + tail logs
#
#   add --agenda to any start mode to also run the always-listening visit agenda
#   tracker (Whisper + db.py topics -> agenda_state.json), e.g. ./monitor.sh --agenda
#
#   add --veeva to any start mode to also run the clinical-trial (Veeva) pipeline:
#     visit_pipeline.py loops edc_autofill + oversight so edc_forms.json,
#     soa_state.json and oversight_state.json stay live. e.g. ./monitor.sh --veeva
#     (compose freely, e.g. ./monitor.sh --agenda --veeva)
#
# Logs: logs/monitor_phone.log | logs/monitor_video.log, logs/monitor_voice.log,
#       logs/ar_bridge.log, logs/agenda.log, logs/veeva.log
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOGDIR="$HERE/logs"; mkdir -p "$LOGDIR"
PIDFILE="$HERE/.monitor.pids"
CAMERA="${CAMERA:-1}"
MIC="${MIC:-1}"

[ -f "$HERE/.env" ] && { set -a; source "$HERE/.env"; set +a; }

# Pull --agenda / --veeva out of the args (they compose with any start mode).
AGENDA=0
VEEVA=0
_args=()
for a in "$@"; do
  case "$a" in
    --agenda) AGENDA=1 ;;
    --veeva)  VEEVA=1 ;;
    *) _args+=("$a") ;;
  esac
done
set -- ${_args[@]+"${_args[@]}"}

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
  pkill -f monitor_phone.py 2>/dev/null || true
  pkill -f conversation_tracker.py 2>/dev/null || true
  pkill -f visit_pipeline.py 2>/dev/null || true
  pkill -f "iproxy 18765" 2>/dev/null || true
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
  echo "--- phone monitor log (tail) ---"; tail -5 "$LOGDIR/monitor_phone.log" 2>/dev/null || true
  echo "--- agenda log (tail) ---"; tail -5 "$LOGDIR/agenda.log" 2>/dev/null || true
  echo "--- veeva log (tail) ---"; tail -5 "$LOGDIR/veeva.log" 2>/dev/null || true
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

start_agenda() {
  echo "▶ starting agenda tracker (patient ${PATIENT:-P001}, mic $MIC) ..."
  ( conda activate papagei_env
    exec python -u "$HERE/conversation_tracker.py" --patient "${PATIENT:-P001}" --mic "$MIC" \
      --out "$HERE/vitals-dashboard/public/agenda_state.json"
  ) >"$LOGDIR/agenda.log" 2>&1 &
  echo $! >> "$PIDFILE"
}

start_veeva() {
  echo "▶ starting Veeva pipeline (subject ${SUBJECT:-S-001}, visit ${VISIT:-V2}) ..."
  ( conda activate papagei_env
    exec python -u "$HERE/visit_pipeline.py" --subject "${SUBJECT:-S-001}" --visit "${VISIT:-V2}"
  ) >"$LOGDIR/veeva.log" 2>&1 &
  echo $! >> "$PIDFILE"
}

if [ "${1:-}" = "--phone" ]; then
  echo "▶ starting phone monitor (Vitals AR app over USB, patient ${PATIENT:-P001}) ..."
  ( conda activate papagei_env
    exec python -u "$HERE/monitor_phone.py" --patient "${PATIENT:-P001}"
  ) >"$LOGDIR/monitor_phone.log" 2>&1 &
  echo $! >> "$PIDFILE"
  [ "$AGENDA" = 1 ] && start_agenda
  [ "$VEEVA" = 1 ] && start_veeva
  echo ""
  echo "✅ live. Plug in the iPhone, open Vitals AR → ⚙︎ → Live (Mac pipeline)."
  echo "   Models warm up for ~25 s first.  tail -f $LOGDIR/monitor_phone.log"
  echo "   stop with: ./monitor.sh --stop"
  exit 0
fi

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

echo "▶ starting AR bridge → phone over USB (patient ${PATIENT:-P001}) ..."
( conda activate papagei_env
  exec python -u "$HERE/ar_bridge.py" --patient "${PATIENT:-P001}"
) >"$LOGDIR/ar_bridge.log" 2>&1 &
echo $! >> "$PIDFILE"

[ "$AGENDA" = 1 ] && start_agenda
[ "$VEEVA" = 1 ] && start_veeva

echo ""
echo "✅ live. PIDs: $(tr '\n' ' ' < "$PIDFILE")"
echo "   reading.json is updating ~every 2s; open the dashboard and toggle LIVE."
echo "   iPhone: plug in, open Vitals AR → ⚙︎ → Live (Mac pipeline)."
echo "   tail -f $LOGDIR/monitor_video.log"
echo "   stop with: ./monitor.sh --stop"
