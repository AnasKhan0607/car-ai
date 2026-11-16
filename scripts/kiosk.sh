#!/usr/bin/env bash
# Launch Car AI fullscreen on the Pi's own display.
#
# Run this from the Pi's desktop session, not over SSH -- Chromium needs a
# display to draw on. For an SSH-friendly start, run app.py alone and open the
# dashboard from any browser on the network.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${CAR_AI_PORT:-5000}"
VENV="${CAR_AI_VENV:-$HOME/car_ai_env}"

if [[ -f "$VENV/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
fi

# Stop the screen blanking mid-drive. Harmless if the tools are absent.
xset s off      2>/dev/null || true
xset -dpms      2>/dev/null || true
xset s noblank  2>/dev/null || true

cd "$HERE"
python app.py --http-port "$PORT" --kiosk "$@"
