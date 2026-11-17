#!/usr/bin/env bash
# Make the Pi tolerate the car cutting power: keep the root filesystem
# writable (owner's choice, #32) but write to the SD card as little as
# possible, and repair it on boot. Safe to re-run; prints what it changes.
#
#   sudo ./scripts/pi-harden.sh
#
# Each step says how to undo it. Containers:
#   docker update --restart=unless-stopped homeassistant mediamtx && docker start homeassistant mediamtx
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "run with sudo"; exit 1; }

# 1. Containers the car doesn't need. Stopped and kept from restarting, not
#    deleted: Home Assistant alone wrote most of the card's idle traffic.
if command -v docker >/dev/null; then
  for c in homeassistant mediamtx; do
    if docker inspect "$c" >/dev/null 2>&1; then
      docker update --restart=no "$c" >/dev/null
      docker stop "$c" >/dev/null 2>&1 || true
      echo "container $c: stopped, restart=no"
    fi
  done
fi

# 2. No automatic package updates. PackageKit refreshed ~120 MB of package
#    cache on EVERY boot -- in a car that's every drive, right in the window
#    where power gets cut. Update deliberately instead (sudo apt update ...).
#    Undo: systemctl unmask packagekit && systemctl enable --now apt-daily.timer apt-daily-upgrade.timer
for unit in packagekit.service apt-daily.timer apt-daily-upgrade.timer; do
  if systemctl list-unit-files "$unit" >/dev/null 2>&1; then
    systemctl stop "$unit" 2>/dev/null || true
    systemctl disable "$unit" >/dev/null 2>&1 || true
  fi
done
systemctl mask packagekit.service >/dev/null 2>&1 || true
echo "automatic updates: off (packagekit masked, apt timers disabled)"

# 3. System journal in RAM, capped. Logs from before a power cut are lost;
#    the drive log (SQLite) is what has to survive, not the journal.
mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/car-ai.conf <<'CONF'
[Journal]
Storage=volatile
RuntimeMaxUse=48M
CONF
systemctl restart systemd-journald
echo "journald: volatile, capped at 48M"

# 4. Filesystem repairs itself on boot after an unclean shutdown.
if grep -q "fsck.repair=yes" /boot/firmware/cmdline.txt; then
  echo "fsck.repair=yes: already set"
else
  sed -i '1 s/$/ fsck.repair=yes/' /boot/firmware/cmdline.txt
  echo "fsck.repair=yes: added to cmdline.txt"
fi

# 5. No access-time writes on every read.
findmnt -no OPTIONS / | grep -q noatime && echo "root: noatime" \
  || echo "WARNING: root is not mounted noatime; add it in /etc/fstab"
