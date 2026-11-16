# Car AI — working notes

On-device OBD-II dashboard + diagnostic assistant for a Raspberry Pi 5 in a car.
Polls the vehicle every 5s, renders a glanceable dashboard, alerts on
out-of-range readings, logs each drive, and answers plain-English questions via
a local `llama3.2:3b` under Ollama.

**Brain decision (2026-11-15):** Claude becomes the main brain (Claude Code
headless, `claude -p`, on the owner's subscription), with Ollama as the offline
fallback and a canned reply below that. This replaces the original "no cloud"
rule with **"must work fully offline"**: cars lose signal exactly where you
need the answer, so the local path is a first-class path, not a degraded one.
Claude gets code-written summaries, never the raw telemetry stream. Built in
#7: `car_ai/brain.py` (Claude, one `claude -p` call per question, 30 s cap,
backoff per failure kind), then Ollama, then a code-written readings summary.
`--no-claude` for local only. The answer's `brain` field says which one
answered, and the chat shows it.

**Terms (checked 2026-11-16, `docs/claude-terms.md`; owner chose the subscription):** the owner using the
unmodified `claude` binary with their own Pro/Max login, for their own car, is
allowed. Calls must stay event-driven ("ordinary, individual usage"); never
lift the OAuth token into our own client; sharing or selling the project means
each user brings their own login or an API key. `--bare` (API key only) is
slated to become the `-p` default, so the brain must accept
`ANTHROPIC_API_KEY` too and fall through to Ollama on an auth error.

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
| The model can't be trusted with a number | Seen on the Pi (#6): "50 km/h" parked, "35 kPa" tyre pressure (it was fuel %), rpm reported as km/h. So in code: `not_on_obd()` answers tyre/oil-level/brake questions without the model; `invented_numbers()` checks every number **and its unit** against the tool results and swaps in `readings_summary()` if one is made up; mock answers get the "simulated" label in code. Prompt rules alone did not hold. |
| Trim never drops the current question | It did, because `get_all_readings` alone exceeded the budget, and the model answered a question nobody asked. That tool now returns a compact form (context only for non-ok readings) plus fault codes and alerts. |
| `temperature: 0` | Diagnostic read-back, not prose. Set with `num_ctx` in `MODEL_OPTIONS`. |
| One prompt file | `car_ai/system_prompt.txt` holds the rules for **both** brains; each fills in only how it gets readings (`VIA_TOOLS` / `VIA_JSON` in `prompt.py`). The car's name comes from `VEHICLE_NAMES` by profile. Edit rules there, never in a brain. |
| Every broad tool carries the problems | `get_all_readings` and `get_vehicle_status` both include fault codes and alerts (`problems()` in `tools.py`). The 3B model answered "is anything wrong?" from the status tool alone and said "healthy" during a misfire. A local answer given without any tool call is replaced with the readings. |
| Claude runs from an empty dir, no tools | `claude -p` from `~/car-ai` would load this repo's CLAUDE.md, hooks and skills. Runs from `~/.local/share/car-ai/claude-cwd` with `--tools "" --strict-mcp-config --no-session-persistence` and our own `--system-prompt`. Not `--bare`: that ignores the subscription login. |
| Remember Claude failures | Backoff by kind (`BACKOFF_S`): auth 10 min, usage limit 30 min, timeout 2 min. In a dead zone the first question waits up to 30 s; later ones go straight to Ollama. Restart the service after logging in to clear an auth backoff. |
| Fuel level on BMW | PID `012F` is optional and many BMWs don't expose it, but this 430i does (read 40% live). Fuel rate (`015E`) is the one it lacks: permanent `unsupported`. The mock still marks fuel level unsupported deliberately, to exercise that path. |
| Flask `threaded=True` | A model answer occupies a request for many seconds; the dashboard must keep refreshing. |
| Bind `::`, not `0.0.0.0` | The iPhone hotspot is IPv6-only; an IPv4-only server can't be opened from a phone in the car. `default_host()` probes for IPv6 and falls back to `0.0.0.0`. `--host 127.0.0.1` still restricts to the Pi. |

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
  CSV logging, **live reads from the 430i** (2026-11-15, parked and idling:
  CH340 ELM327 at `/dev/ttyUSB0`, CAN 11/500, 111 PIDs).
- ✅ Chat loop end-to-end on the Pi (2026-11-16, #6), with the guards in the
  constraints table. `llama3.2:3b` is still slow (25–65 s) and sometimes
  pins a real number on the wrong sensor.
- ⚠️ Unproven: live reads *while driving*; the 800×480 in-car layout (kiosk itself is
  verified: boots fullscreen on the 7" touchscreen via `car-ai.service` +
  `scripts/car-ai-dashboard.desktop`).
- Coolant on this car runs ~108–110 °C at idle (BMW map thermostat, normal).
  Handled by the `bmw_b46` vehicle profile, which the Pi's service enables;
  confirmed no false alert live, parked and idling.

## Gotchas when debugging on hardware

- `No OBD-II adapter responding` → `lsusb`, `ls /dev/ttyUSB*`, `groups` (need
  `dialout`; `sudo usermod -aG dialout $USER` then log in again). The adapter
  is powered by the car — it must be plugged in with the ignition on.
- Speed and RPM read `--` with the ignition merely in accessory. Engine must run.
- Chromium on the Pi spews `gbm_wrapper ... dma_buf` and `DEPRECATED_ENDPOINT`
  errors. All noise; it renders fine.
- Only one process can hold the adapter. Don't run two instances. On boot,
  `car-ai.service` already holds it: `sudo systemctl stop car-ai` before
  running `app.py` by hand.
- Over SSH, `pkill -f <pattern>` matches the SSH command's own shell and kills
  the session, including when the same command line *starts* the process
  (`... app.py --mock ...; pkill -f "[a]pp.py --mock"` still matches). Start
  with `& echo $! > /tmp/x.pid` and `kill $(cat /tmp/x.pid)`, or `pkill -x`.
- A second instance with `--mock --http-port 5001 --no-log` can run next to
  the live service: the simulator never opens the adapter.
- Touch dead but picture fine on the 7" display → the thin ribbon from the
  touch glass to the driver board is loose (`edt_ft5x06 ... error: -121`).
  Don't `modprobe -r edt_ft5x06`; it shares the display's power controller
  and blanked the screen.

## Workflow

- Plan: `docs/PHASES.md`. Each task is an issue on the fork
  **`AnasKhan0607/car-ai`** (always pass `--repo AnasKhan0607/car-ai` to `gh`:
  in a fork it can default to the read-only upstream `iqureshi123/car-ai`).
- Branch `issue-N-slug` off `origin/main`, conventional commits referencing
  `#N`, PR into `main` with `Closes #N`. Commit as you go.
- `/doc-check` before `gh pr create`. A hook in `.claude/settings.json` blocks
  PRs that change code without touching docs.
- CI (`.github/workflows/ci.yml`) runs `--selftest` with **no** third-party
  packages installed, so keep the self-test dependency-free.

| Skill / agent | Use for |
|---|---|
| `project-management` | Issues, branches, PRs, status-table rules |
| `car-ai-feature` | Building anything beyond a sensor; layers and invariants |
| `add-sensor` | A new OBD value: one `sensors.py` entry |
| `car-ai-run` | Running and screenshotting the app |
| `deploy-to-pi` | Getting code onto the Pi and restarting it |
| `doc-check` | Pre-PR doc review |
| `project-manager` agent | Planning phases into issues, "what's next" |

## Conventions

- Comments explain *why*, especially where the code looks odd on purpose (the
  no-fallback rule, the poll tiers, the trimming). Don't strip them.
- Everything user-facing is written for a driver, not an engineer: say what the
  reading means and what to do, not just the number.
- Thresholds in `sensors.py` are deliberately conservative and vehicle-generic.
  A car that runs differently gets an entry in `VEHICLE_PROFILES` (selected by
  `CAR_AI_VEHICLE`, set in `scripts/car-ai.service`), not a retune of the
  generic values. Override `context` with any threshold: the model reads it.
  `CAR_AI_VEHICLE=bmw_b46 python app.py ...` to run with it by hand.
