# Car AI — working notes

On-device OBD-II dashboard + diagnostic assistant for a Raspberry Pi 5 in a car.
Polls the vehicle every 5s, renders a glanceable dashboard, alerts on
out-of-range readings, logs each drive, and answers plain-English questions via
a local `llama3.2:3b` under Ollama. No cloud, by design — cars lose signal
exactly where you need the answer, and vehicle telemetry is location data.

## The invariant — read before changing anything in `source.py`

**Live data and simulated data are never mixed.**

The version this replaced fell back to a hardcoded number whenever a live read
returned null, with nothing marking it fake. Parked in a driveway with no
adapter attached, it reported *"you are currently travelling at 72 km/h"* as a
confident, complete sentence. A diagnostic tool that invents a plausible number
is worse than one that admits it does not know.

Concretely, and non-negotiably:

- Simulated data requires an explicit `--mock`. There is no automatic fallback.
- A failed live read surfaces as `status: no_data` / `error` — never a number.
- A PID the car does not implement is detected once at connect time and marked
  `unsupported` permanently, so it reads as "this car doesn't have it" rather
  than an intermittent fault.
- `mode: "mock"` propagates to the UI pill, every tool result's `data_source`,
  and the model's own system prompt.

If you are tempted to add a "sensible default" for a missing reading: don't.
Return the unavailability and let the UI and the model say so.

## Architecture

```
ELM327 (USB) ──► polling thread (owns the serial port, 5s)
                         │ publishes an immutable snapshot
         ┌───────────────┼────────────────┐
         ▼               ▼                ▼
     dashboard        monitor         LLM tools
     (browser)     alerts + CSV     (llama3.2:3b)
```

- **One thread owns the connection.** An ELM327 serves one request at a time
  and one client at a time. Everything else reads the snapshot. Two payoffs:
  the dashboard and the assistant cannot contend for the port, and an LLM
  question never blocks on serial I/O.
- **Tools read the snapshot, not the car.** Tool calls are instant. The
  trade-off (a reading may be up to one interval old) is surfaced —
  `reading_age_s` on every result, `stale` on the tile.
- **Poll tiers.** Each PID costs ~50–100 ms on an ELM327. Speed/rpm/coolant/
  load every cycle (`TIER_PRIMARY`), slower values every 3rd, DTCs every 6th.

## `sensors.py` is the single source of truth

The poller, the dashboard tiles, the alert thresholds, and the LLM tool schemas
are **all** derived from `SENSORS` in `car_ai/sensors.py`. Adding a sensor is
one dataclass entry — never edit four files. See the `add-sensor` skill.

## Hard-won constraints

| Constraint | Why it matters |
|---|---|
| `ollama>=0.5` | `tool_name` on tool-result messages doesn't exist on 0.4.x and is **silently dropped**. Two tool calls in one turn then give the model two anonymous blobs. |
| `num_ctx=4096` | What Ollama serves this model with. `assistant.py` trims history and keeps the system prompt *outside* `self._messages` so it can't be evicted. |
| Cap tool rounds | A 3B model will loop. `MAX_TOOL_ROUNDS=4`, then return what was read. |
| Never bare numbers | Tool results carry value + unit + normal range. Handed `98`, a 3B model invents what it means. |
| Pass DTC descriptions through | python-OBD's description is authoritative. A 3B model asked to explain `P0128` from memory produces something fluent and wrong. |
| Dispatcher never raises | Unknown tool name returns a structured error listing the real tools, so the model self-corrects instead of killing the turn. |
| Fuel level on BMW | PID `012F` is optional and BMW generally doesn't expose it. Expect permanent `unsupported` on the 430i. The mock reproduces this deliberately. |
| Flask `threaded=True` | A model answer occupies a request for many seconds; the dashboard must keep refreshing. |

## Commands

```bash
python app.py --selftest      # tools + alerts, no adapter, no model, no deps
python app.py --mock          # simulated car, full dashboard on :5000
python app.py --mock --scenario overheat
python app.py                 # live car, autoscan for the adapter
python app.py --serial-port /dev/ttyUSB0
./scripts/kiosk.sh            # fullscreen on the Pi's display (needs a display)
```

Scenarios: `normal`, `overheat`, `low_fuel`, `misfire`, `charging_fault`. They
exist to exercise the alerting and the model's judgement — the part you cannot
test on a healthy car.

## Status — keep this honest

The README's status table is the contract with whoever reads this repo. It
distinguishes ✅ verified / ⚠️ written-but-unproven / ⛔ not started. **Do not
promote a row to ✅ without actually running it.** As of the last session:

- ✅ Verified: tool layer, alert engine (incl. debounce + escalation, confirmed
  on the Pi), mock scenarios, dashboard rendering and alert states, HTTP API,
  CSV logging.
- ⚠️ Unproven: the **LLM chat loop has still never been run end-to-end**;
  **live reads from a vehicle have never succeeded** (the ELM327 has never been
  detected — a USB/`dialout` issue to work through at the car); kiosk visible
  on screen; the 800×480 in-car layout.

## Gotchas when debugging on hardware

- `No OBD-II adapter responding` → `lsusb`, `ls /dev/ttyUSB*`, `groups` (need
  `dialout`; `sudo usermod -aG dialout $USER` then log in again). The adapter
  is powered by the car — it must be plugged in with the ignition on.
- Speed and RPM read `--` with the ignition merely in accessory. Engine must run.
- Chromium on the Pi spews `gbm_wrapper ... dma_buf` and `DEPRECATED_ENDPOINT`
  errors. All noise; it renders fine.
- Only one process can hold the adapter. Don't run two instances.

## Conventions

- Comments explain *why*, especially where the code looks odd on purpose (the
  no-fallback rule, the poll tiers, the trimming). Don't strip them.
- Everything user-facing is written for a driver, not an engineer: say what the
  reading means and what to do, not just the number.
- Thresholds in `sensors.py` are deliberately conservative and vehicle-generic.
