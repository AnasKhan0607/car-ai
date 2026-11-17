#!/usr/bin/env bash
# Browser half of the boot setup: open the dashboard fullscreen on the Pi's
# screen once the server is up.
#
# car-ai.service (systemd) runs the server; this script only shows it, and is
# run from the desktop autostart because a system service has no display.
# Kept separate from kiosk.sh on purpose: kiosk.sh starts its own app.py, and a
# second instance would fight the service for the one-client ELM327.
#
# Install: copy scripts/car-ai-dashboard.desktop to ~/.config/autostart/
set -uo pipefail

PORT="${CAR_AI_PORT:-5000}"
URL="http://localhost:$PORT"

# The service may still be starting (it waits for Ollama). Without this wait
# Chromium would open on a connection-refused page and stay there.
for _ in $(seq 90); do
  curl -sf -o /dev/null "$URL" && break
  sleep 1
done

# The car cuts power without a clean shutdown every time, so suppress the
# "Chromium didn't shut down correctly -- Restore?" bubble that would
# otherwise sit over the dashboard after every drive.
#
# The profile lives in RAM (tmpfs under /run/user): on the SD card Chromium
# wrote ~1.2 MB a minute of cache and session state, and a profile caught
# mid-write by a power cut can come back corrupt. Nothing in it needs to
# survive a reboot -- the dashboard reloads from this Pi.
PROFILE="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/car-ai-kiosk"
mkdir -p "$PROFILE"
exec chromium --kiosk --noerrdialogs --disable-infobars \
  --hide-crash-restore-bubble --ozone-platform=wayland \
  --user-data-dir="$PROFILE" --disk-cache-dir="$PROFILE/cache" "$URL"
