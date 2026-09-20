#!/usr/bin/env bash
# ONE COMMAND to set up + run the live pipeline and connect to the iPhone AR app.
#
#   ./start.sh            # iPhone Vitals AR app is the sensor (over USB)  [default]
#   ./start.sh --mac      # use the Mac camera/mic instead (no phone needed)
#   ./start.sh --stop     # stop everything
#
# Real model, NOT the demo scenario. Prints a judge-facing summary when live.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"
DEVICE_ID="F97FF05B-C07A-507A-8A40-4ED34C73E269"   # this iPhone (for devicectl)

[ -f "$HERE/.env" ] && { set -a; source "$HERE/.env"; set +a; }

# --- find + source conda -----------------------------------------------------
for c in "${CONDA_SH:-}" /opt/miniconda3 /opt/homebrew/anaconda3 /opt/anaconda3 "$HOME/miniconda3" "$HOME/anaconda3"; do
  [ -n "$c" ] && [ -f "${c%/etc/profile.d/conda.sh}/etc/profile.d/conda.sh" ] && { source "${c%/etc/profile.d/conda.sh}/etc/profile.d/conda.sh"; break; }
done

if [ "${1:-}" = "--stop" ]; then
  pkill -f demo_override.py 2>/dev/null || true
  ./monitor.sh --stop
  exit 0
fi

# --- 0. first-run setup if the envs are missing ------------------------------
if ! conda env list 2>/dev/null | grep -q "^rppg " || ! conda env list 2>/dev/null | grep -q "^papagei_env "; then
  echo "▶ first-time setup — creating conda envs + installing deps (a few minutes)..."
  ./setup_envs.sh
fi

# --- 1. dashboard (Vite) — start if not already serving ----------------------
if ! curl -s -o /dev/null --max-time 2 http://127.0.0.1:5199/ 2>/dev/null; then
  echo "▶ starting dashboard on http://localhost:5199 ..."
  ( cd vitals-dashboard && nohup npm run dev -- --port 5199 --host 0.0.0.0 >/tmp/vitals-dash.log 2>&1 & )
  sleep 4
fi

# --- 2. clear any demo / stale run -------------------------------------------
pkill -f demo_override.py 2>/dev/null || true
./monitor.sh --stop >/dev/null 2>&1 || true
sleep 1

# --- 3. live pipeline (real model) -------------------------------------------
if [ "${1:-}" = "--mac" ]; then
  echo "▶ launching LIVE pipeline on the Mac camera + full Veeva/agenda stack ..."
  CAMERA="${CAMERA:-0}" ./monitor.sh --veeva --agenda >/dev/null 2>&1
  PHONE=0
else
  echo "▶ (re)installing + launching the Vitals AR app on the iPhone ..."
  APP=$(find ~/Library/Developer/Xcode/DerivedData/VitalsAR-*/Build/Products/Debug-iphoneos -maxdepth 1 -name "VitalsAR.app" 2>/dev/null | head -1)
  if [ -n "$APP" ]; then
    xcrun devicectl device install app --device "$DEVICE_ID" "$APP" >/dev/null 2>&1 \
      && xcrun devicectl device process launch --device "$DEVICE_ID" com.shahatitjacob.vitalsar >/dev/null 2>&1 \
      && echo "   app launched on the phone" || echo "   (couldn't auto-launch — open Vitals AR on the phone manually)"
  fi
  echo "▶ launching LIVE pipeline (iPhone AR app = sensor) + Veeva/agenda ..."
  ./monitor.sh --phone --veeva --agenda >/dev/null 2>&1
  PHONE=1
fi

sleep 2
echo ""
echo "════════════════════════════════════════════════════════════════════════"
cat "$HERE/WHATS_RUNNING.md" 2>/dev/null
echo "════════════════════════════════════════════════════════════════════════"
echo ""
echo "  ▸ Dashboard:  http://localhost:5199   →  click  ● Go live"
if [ "$PHONE" = 1 ]; then
  echo "  ▸ iPhone:     unlock, keep Vitals AR on-screen  →  ⚙︎  →  Live (Mac pipeline)"
  echo "                (its AR overlay + the dashboard mirror the same live pipeline)"
fi
echo "  ▸ Logs:       tail -f logs/monitor_${PHONE:+phone}${PHONE:+.log}"
echo "  ▸ Stop:       ./start.sh --stop"
echo ""
