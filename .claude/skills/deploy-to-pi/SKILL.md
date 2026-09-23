---
name: deploy-to-pi
description: Get code changes from a dev machine onto the Raspberry Pi running Car AI, and restart it there. Use when asked to deploy, push to the Pi, update the Pi, or test a change on real hardware.
---

# Deploying to the Pi

## The setup

- Pi: `master@pi5.local` (`hostname -I` for the IP). **Password auth** — key
  auth is not set up, so an automated `ssh`/`scp` from a non-interactive shell
  will fail with `Permission denied (publickey,password)`. Either run the
  command in an interactive terminal, or set up keys once:
  `ssh-copy-id master@pi5.local`.
- Repo on the Pi: `~/car-ai`, cloned from `AnasKhan0607/car-ai`.
- venv on the Pi: `~/car_ai_env`.
- Ollama runs as a systemd service on the Pi with `llama3.2:3b` pulled.

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

## Running it persistently

Over SSH the process dies with the session. Use tmux:

```bash
tmux new -s car          # then run app.py inside
tmux attach -t car       # reattach later
```

For start-on-boot see `scripts/car-ai.service` (server only — a system service
has no display, so kiosk goes in the desktop autostart instead).
