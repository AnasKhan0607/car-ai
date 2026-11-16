# Car AI

An on-device diagnostic dashboard for cars. It polls your car's OBD-II port
every few seconds, shows the readings on a screen you can glance at while
driving, warns you when something goes out of range, and lets you ask it
questions in plain English — *"is my engine running hot?"* — which a local LLM
answers by reading the actual sensors.

No cloud. The model, the tools, and the logic all run on the device.

---

## Status — read this first

**Designed for a Raspberry Pi 5 (16GB) with an ELM327 USB adapter.**

| Component | State |
|---|---|
| Sensor catalogue, alert thresholds, tool schemas | ✅ Verified by self-test |
| Alerting: thresholds, debounce, warn→crit escalation | ✅ Verified on the Pi |
| Graceful recovery from hallucinated tool names | ✅ Verified by self-test |
| Simulated vehicle with fault scenarios (`--mock`) | ✅ Verified end-to-end |
| Dashboard rendering, live refresh, alert states | ✅ Verified in a browser |
| HTTP API (`/api/state`, `/api/chat`) | ✅ Verified end-to-end |
| Drive logging to CSV | ✅ Verified on the Pi |
| Full LLM chat loop against Ollama | ✅ Runs end-to-end on the Pi, live and simulated; 25–65 s per answer. See note below. |
| Live OBD-II reads from a real vehicle | ✅ Verified on the 430i (parked, engine idling) |
| Kiosk mode on the Pi's display, starting on boot | ✅ Verified on the Pi's 7" touchscreen after a cold reboot |
| Layout on a small (800×480) in-car screen | ⚠️ CSS written; not yet verified on hardware |

Everything marked ✅ was exercised against the simulator and a running server;
the alert engine and drive logging were additionally confirmed on the Pi itself
(a simulated overheat crossed the warn threshold, survived debounce, and
escalated to critical ~80s later).

Live reads were confirmed on the 430i on 2026-11-15, parked with the engine
idling: the ELM327 negotiated CAN 11-bit/500k, the car reported 111 supported
PIDs, and every dashboard sensor read except `fuel_rate` (correctly shown as
unsupported). Readings while actually driving are not yet verified. Treat the
⚠️ rows as unproven.

The chat loop was first run against Ollama on the Pi on 2026-11-16, in the
parked car and against the simulator (`scripts/chat_check.py`). The first run
invented a dirty air filter, low oil and "50 km/h" while parked. After the
fixes in #6 it uses the right tools, refuses things OBD can't read, labels
simulated data, and any answer containing a number no tool returned (or a
real number with the wrong unit) is replaced by a code-written summary.
Remaining weakness: `llama3.2:3b` can still pin a real reading on the wrong
sensor (oil temperature called "engine temperature") and is slow (25–65 s).
That's the case for the Claude brain in Phase 2.

---

## The rule that shapes this codebase

**Live data and simulated data are never mixed.**

An earlier version fell back to a hardcoded number whenever a live sensor read
failed, with nothing in the output to say so. Sitting in a house with no
adapter plugged in, it would report *"you are currently travelling at 72 km/h"*
in a confident, complete sentence.

That is worse than not running. So:

- Simulated data requires an explicit `--mock` flag.
- In live mode a failed read is reported as a failed read — on the dashboard,
  in the API, and to the model, which is instructed never to estimate.
- A sensor the car does not implement is detected once at connect time and
  shown permanently as *"not supported by this car"*, not as an intermittent
  fault.
- Everything simulated is labelled `simulated data` in the UI and in the
  model's own system prompt.

### On BMW and fuel level

Fuel level (PID `012F`) is **optional** in the OBD-II spec, and many BMWs do
not expose it. This 430i turned out to: it read 40% on the first live
connection. What it does *not* expose is fuel rate (PID `015E`), which shows
`n/s — not supported by this car`, permanently. That is the car, not a bug.
The `--mock` simulator still reports fuel level as unsupported deliberately, so
the unsupported-sensor path is exercised during development.

---

## How it works

```
        ELM327 (USB)
             │
    ┌────────▼─────────┐   one thread owns the serial port
    │  polling thread  │   queries the car every 5s
    └────────┬─────────┘
             │ publishes an immutable snapshot
     ┌───────┴────────┬──────────────────┐
     ▼                ▼                  ▼
  dashboard        monitor          LLM tools
  (browser)     alerts + CSV     (llama3.2:3b)
```

**One thread owns the connection.** An ELM327 serves one request at a time and
one client at a time. A single poller holds the port and publishes a snapshot;
the dashboard and the model both read that snapshot and never touch the serial
port. Two consequences: the two halves of the app cannot fight over the
adapter, and an LLM question never waits on serial I/O.

**Tools read the snapshot, not the car.** A tool call is instant. The trade-off
— a reading can be up to one poll interval old — is made visible: every result
carries its age, and stale readings say so.

**Poll tiers.** Each PID costs roughly 50–100 ms on an ELM327, so polling
everything every cycle would make the dashboard sluggish for no benefit. Speed,
rpm, coolant and load refresh every cycle; slower-moving values every third;
fault codes every sixth.

### Why the tools return dicts

Every tool result carries the value, its unit, and its normal range:

```json
{"value": 98, "unit": "°C", "status": "ok",
 "normal_range": "60-105 °C",
 "context": "Engine coolant temperature. Normal operating range is ~90-104 C.
             Above ~110 C is a genuine overheating risk..."}
```

Hand a 3B model the bare integer `98` and it will confidently invent what that
means. Grounded like this, it doesn't have to.

The same applies to fault codes: python-OBD's own description for each DTC is
passed through and marked authoritative, because a 3B model asked to explain
`P0128` from memory will produce something fluent and wrong.

---

## Running it

### No car, no adapter, no model

```bash
python app.py --selftest
```

Exercises every tool, the alert engine, and the hallucinated-tool-name recovery
path. Needs nothing installed beyond the standard library.

### Simulated car, full dashboard

```bash
python -m venv car_ai_env
source car_ai_env/bin/activate
pip install -r requirements.txt

python app.py --mock                        # healthy car
python app.py --mock --scenario overheat    # coolant climbing into the red
```

Open <http://localhost:5000>. Scenarios: `normal`, `overheat`, `low_fuel`,
`misfire`, `charging_fault` — they exist so the alerting and the assistant's
judgement can be tested, which is the part you cannot test on a healthy car.

### Real car

```bash
ollama pull llama3.2:3b          # ~2 GB, once
python app.py                    # autoscans for the adapter
python app.py --serial-port /dev/ttyUSB0   # or name it explicitly
```

### Fullscreen on the Pi's screen

```bash
./scripts/kiosk.sh               # from the Pi's desktop session, not SSH
```

### Start on boot

Two halves, because a systemd service has no display to draw on:

```bash
# server: runs headless, restarts on failure
sudo cp scripts/car-ai.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now car-ai

# browser: waits for the server, then opens the dashboard fullscreen
cp scripts/car-ai-dashboard.desktop ~/.config/autostart/
sudo raspi-config nonint do_blanking 1   # stop the screen going dark mid-drive
```

Do **not** autostart `scripts/kiosk.sh` alongside the service: it launches its
own `app.py`, and two instances fight over the adapter. To leave kiosk mode
without a keyboard, `ssh pi5.local pkill -x chromium`.

---

## Alerts and logging

The monitor runs off the same 5-second poll and watches on its own — the thing
a question-and-answer chatbot fundamentally cannot do, since coolant creeping
up over ten minutes is exactly what a driver will not think to ask about.

- Readings are checked against the ranges in `car_ai/sensors.py`.
- Alerts are **debounced**: a value must stay out of range for two consecutive
  cycles. ELM327 adapters return the occasional garbage sample, and a dashboard
  that cries wolf gets ignored at the moment it matters.
- A **new** fault code alerts immediately — the ECU has already debounced it.
- Losing the adapter is itself an alert, so a dropped connection never looks
  like a screen full of healthy frozen numbers.
- Every cycle is appended to `~/car_ai_logs/drive_<timestamp>.csv`.

---

## Troubleshooting

**`No OBD-II adapter responding`** — check, in order: `lsusb` (does it
enumerate), `ls /dev/ttyUSB*` (did it get a device), `groups` (are you in
`dialout`; if not, `sudo usermod -aG dialout $USER` and log in again). The
adapter must be plugged into the car with the ignition on — most ELM327s are
powered by the car, not by USB.

**Speed and RPM read `--`** — the engine is not running. Those PIDs return
nothing with the ignition merely in accessory.

**`Could not reach the model`** — `systemctl status ollama`, and
`ollama list` to confirm `llama3.2:3b` is pulled.

**Answers get worse in a long conversation** — shouldn't happen: history is
trimmed to fit the 4096-token window with the system prompt pinned outside it.
If it does, `clear` in the chat panel starts fresh.

---

## Layout

```
app.py                  entry point, CLI, self-test
car_ai/sensors.py       the sensor catalogue — one source of truth
car_ai/source.py        connection + polling thread; live and mock sources
car_ai/monitor.py       alert evaluation and CSV drive logging
car_ai/tools.py         tool schemas and dispatcher for the LLM
car_ai/assistant.py     the Ollama chat loop
car_ai/server.py        Flask routes
car_ai/templates/, static/    the dashboard
```

Adding a sensor is a single entry in `car_ai/sensors.py`: the poller, the
dashboard, the alert thresholds and the LLM tool schemas all derive from it.

See **[CLAUDE.md](CLAUDE.md)** for working notes — the design invariants, the
hard-won constraints (why `ollama>=0.5` is pinned, why history is trimmed the
way it is), and hardware debugging. `.claude/skills/` holds task guides for
running the app, adding a sensor, and deploying to the Pi.

---

## Roadmap

The full phased plan (Claude brain, power safety, voice, trips, ENET) is in
[`docs/PHASES.md`](docs/PHASES.md); work is tracked as issues on this repo.

- [ ] Run the LLM loop end-to-end against Ollama on the Pi
- [x] Verify live reads against the vehicle; confirm which PIDs it answers
- [ ] Measure poll latency and dashboard responsiveness while driving
- [x] Verify the kiosk display on the in-car screen
- [ ] 3D-printed enclosure

---

## Team

- **Ibrahim Qureshi** — AI & software layer: Ollama setup, model selection and
  testing, tool-call design, OBD integration, and Pi environment setup.
- Collaborator — hardware integration and the in-car screen.
- Collaborator — CAD and the 3D-printed enclosure.
