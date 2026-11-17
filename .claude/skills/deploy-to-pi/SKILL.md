---
name: deploy-to-pi
description: Get code changes from a dev machine onto the Raspberry Pi running Car AI, and restart it there. Use when asked to deploy, push to the Pi, update the Pi, or test a change on real hardware.
---

# Deploying to the Pi

## The setup

- Pi: `ssh pi5.local` (user `master`). Key auth via `~/.ssh/pi5_ed25519`,
  configured in the dev Mac's `~/.ssh/config`, so non-interactive `ssh` works.
  Passwordless `sudo` on the Pi.
- On the owner's iPhone hotspot the network is **IPv6-only**: `pi5.local`
  resolves to an IPv6 address only. `scp` has been seen to hang there; pipe
  files over `ssh` instead (`tar cf - files | ssh pi5.local 'cd ~/car-ai &&
  tar xf -'`). Always pass `-n` to `ssh` when not piping stdin.
- Code on the Pi: `~/car-ai` (a plain copy, not a git checkout; see below).
- venv on the Pi: `~/car_ai_env`.
- Ollama runs as a systemd service on the Pi with `llama3.2:3b` pulled.
- Claude Code is installed natively at `~/.local/bin/claude`, logged in to the
  owner's claude.ai account (`claude auth status`). To log in again, the owner
  runs `ssh -t pi5.local '~/.local/bin/claude auth login'` in their own
  terminal. Then `sudo systemctl restart car-ai` to clear the auth backoff.

## Normal path — through git

```bash
# dev machine
git push origin <branch>

# Pi
cd ~/car-ai && git pull && source ~/car_ai_env/bin/activate
pip install -r requirements.txt       # only if requirements changed
python app.py --mock                  # or: python app.py
```

`origin` is the fork `AnasKhan0607/car-ai`. The upstream
`iqureshi123/car-ai` is **read-only for this account** — pushing there fails
with a 403. Changes reach upstream only via a pull request from the fork.

## When git isn't available or you want a dirty working tree on the Pi

```bash
tar --exclude='.git' --exclude='__pycache__' -czf /tmp/car-ai.tar.gz -C ~/Desktop car-ai
scp /tmp/car-ai.tar.gz master@pi5.local:~/      # interactive; prompts for password
# Pi:
cd ~ && tar xzf car-ai.tar.gz
```

Extract to a **fresh directory** rather than over an existing checkout if the
Pi has uncommitted local edits — silently clobbering them is worse than a
second folder.

Note: serving the tarball over HTTP from the dev machine for the Pi to `curl`
is blocked by the sandbox in Claude Code sessions. Don't plan around it.

## Verify after deploying

```bash
python app.py --selftest        # fast, no adapter or model needed
python app.py --mock            # then check http://pi5.local:5000
```

For a live test the adapter must be plugged into the car with the ignition on.
If it isn't detected: `lsusb`, `ls /dev/ttyUSB*`, `groups` (needs `dialout`).

## It's already running: restart, don't start

The Pi boots into `car-ai.service` (server, live, owns the adapter) plus a
desktop autostart (`scripts/car-ai-dashboard.desktop` → `kiosk-browser.sh`)
that opens Chromium fullscreen. After deploying:

```bash
sudo systemctl restart car-ai           # pick up new code
journalctl -u car-ai -n 30 --no-pager   # check it connected
pkill -x chromium                       # leave kiosk (no keyboard on the Pi)
```

To run `app.py` by hand (e.g. `--mock`), `sudo systemctl stop car-ai` first:
two instances fight over the adapter. Changing `scripts/car-ai.service`
needs `sudo cp` to `/etc/systemd/system/` and `daemon-reload`.

## Hardening (run once, re-run after reinstalling the OS)

```bash
sudo ./scripts/pi-harden.sh
```

Cuts SD writes so a power cut has less to corrupt: journald in RAM (so
`journalctl` forgets across reboots; that's the point), PackageKit masked and
apt timers off (update by hand: `sudo apt update && sudo apt full-upgrade`),
Home Assistant / MediaMTX containers stopped with `restart=no`,
`fsck.repair=yes`. Each step prints its undo. The kiosk's Chromium profile
lives in `/run/user/$UID` via `kiosk-browser.sh`, so it is wiped every boot.

Note: `~/car-ai` on the Pi is **not a git checkout** (it was unpacked from a
tarball), so the "through git" path above needs a fresh clone first. Until
then, use the tar-over-ssh path.
