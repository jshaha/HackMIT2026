#!/usr/bin/env bash
# Full fatigue-monitoring pipeline, end to end:
#
#   video  --> open rPPG --> HR / BR / HRV  ┐
#   voice  --> voice decoder --> arousal/fatigue  ├--> agentic loop --> "too fatigued?"
#
# Modes:
#   ./run_pipeline.sh              # full: capture iPhone video + mic voice, then decide
#   ./run_pipeline.sh --offline    # skip capture, decide from existing JSONs (fast test)
#   ./run_pipeline.sh --video-only # capture video leg only, then decide
#   ./run_pipeline.sh --voice-only # capture voice leg only, then decide
#
# Extra args: VID_SECONDS (default 30) and VOICE_SECONDS (default 15)
#   ./run_pipeline.sh 30 15
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load secrets (OPENAI_API_KEY etc.) from .env if present — gitignored.
if [ -f "$HERE/.env" ]; then
  set -a; source "$HERE/.env"; set +a
fi

MODE="full"
case "${1:-}" in
  --offline|--video-only|--voice-only) MODE="${1#--}"; shift ;;
esac
VID_SECONDS="${1:-30}"
VOICE_SECONDS="${2:-15}"

do_video() {
  echo "▶ [1/3] Video leg — iPhone rPPG (${VID_SECONDS}s)"
  "$HERE/capture_iphone.sh" "$VID_SECONDS" "$HERE/iphone_bvp.npz"
}
do_voice() {
  echo "▶ [2/3] Voice leg — mic decoder (${VOICE_SECONDS}s)"
  "$HERE/capture_voice.sh" "$VOICE_SECONDS" 1 voice.wav
}

case "$MODE" in
  full)       do_video; do_voice ;;
  video-only) do_video ;;
  voice-only) do_voice ;;
  offline)    echo "▶ Offline — using existing reading.json / voice_features.json" ;;
esac

echo "▶ [3/3] Agentic fatigue loop"
# Find conda wherever it's installed (Miniconda, Anaconda, Homebrew); override with CONDA_SH=...
for c in "${CONDA_SH:-}" /opt/miniconda3 /opt/homebrew/anaconda3 /opt/anaconda3 "$HOME/miniconda3" "$HOME/anaconda3"; do
  [ -n "$c" ] && [ -f "${c%/etc/profile.d/conda.sh}/etc/profile.d/conda.sh" ] && { source "${c%/etc/profile.d/conda.sh}/etc/profile.d/conda.sh"; break; }
done
set +u  # conda's (de)activate hooks reference unset variables
conda activate papagei_env
READING_JSON="$HERE/vitals-dashboard/public/reading.json"
python -u "$HERE/fatigue_agent.py" \
    --reading "$READING_JSON" \
    --voice "$HERE/voice_features.json" \
    --out "$HERE/decision.json"

echo "▶ Persisting to SQLite (patient=${PATIENT:-P001})"
READING_ARG=(); [ -f "$READING_JSON" ] && READING_ARG=(--reading "$READING_JSON")
python "$HERE/db.py" ingest \
    --patient "${PATIENT:-P001}" \
    "${READING_ARG[@]}" \
    --voice "$HERE/voice_features.json" \
    --decision "$HERE/decision.json"
