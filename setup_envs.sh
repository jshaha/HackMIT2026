#!/usr/bin/env bash
# Create the two conda envs the pipeline uses: 'rppg' (video) and 'papagei_env' (voice + agent + AR bridge).
#   ./setup_envs.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for c in "${CONDA_SH:-}" /opt/miniconda3 /opt/homebrew/anaconda3 /opt/anaconda3 "$HOME/miniconda3" "$HOME/anaconda3"; do
  [ -n "$c" ] && [ -f "${c%/etc/profile.d/conda.sh}/etc/profile.d/conda.sh" ] && { source "${c%/etc/profile.d/conda.sh}/etc/profile.d/conda.sh"; break; }
done
# The classic solver / no plugins sidesteps broken libmamba installs.
export CONDA_SOLVER=classic CONDA_NO_PLUGINS=true
conda env list | grep -q "^rppg " || conda create -y -q -n rppg python=3.11
conda env list | grep -q "^papagei_env " || conda create -y -q -n papagei_env python=3.11
conda run -n rppg pip install -q -r "$HERE/requirements-rppg.txt"
conda run -n papagei_env pip install -q -r "$HERE/requirements-voice.txt"
echo "✅ envs ready. The voice model (~1.2 GB) downloads on first use."
