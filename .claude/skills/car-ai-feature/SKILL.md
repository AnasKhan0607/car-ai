---
name: car-ai-feature
description: End-to-end recipe for building a Car AI feature (anything beyond adding a sensor) — a new AI tool, an alert rule, a trip or report feature, a brain or voice component, a dashboard panel. Covers which layer each piece belongs in, the invariants it must keep, and how to prove it works before the PR.
---

# Building a Car AI feature

Start from an issue (`project-management`). For a new vehicle value, use
`add-sensor` instead: that's one `sensors.py` entry and nothing else.

## Where things go

```
source.py   polling thread owns the ELM327, publishes an immutable VehicleState
   │        (LiveOBDSource / MockSource; build_source() picks one)
   ├──► monitor.py   on_snapshot(): thresholds → Alerts, debounce, CSV log
   ├──► tools.py     build_registry(): zero-arg tools reading source.snapshot()
   │        └──► assistant.py  Ollama loop, SYSTEM_PROMPT, MAX_TOOL_ROUNDS
   └──► server.py    Flask: /api/state, /api/alerts/history, /api/chat(/reset)
                         └──► static/app.js + templates/index.html
```

| Feature kind | Lives in | Never |
|---|---|---|
| New fact the AI can read | `tools.py`, `register(...)` reading `source.snapshot()` | Touch the serial port from a tool |
| Rule or event | `monitor.py` (thresholds come from `sensors.py`) | Call an AI from the monitor loop |
| Something derived over time (trips, score, trends) | New module fed by `monitor.on_snapshot` or the log | Re-poll the car for it |
| AI behaviour (Claude brain, prompts) | Rules in `system_prompt.txt` (both brains); plumbing in `brain.py` / `assistant.py` | Hand the model a raw stream or a bare number; give one brain a rule the other lacks |
| UI | `static/app.js`, `templates/index.html`, `style.css` | Show a value without its status |

## Invariants a feature must keep

1. **One thread owns the adapter.** Everything else reads the snapshot.
2. **No invented numbers.** Missing data surfaces as `no_data` / `error` /
   `unsupported`, including in anything an AI says or a report writes. Don't
   rely on the prompt for this: model output that reaches the driver goes
   through `invented_numbers()` (number **and** unit must match a tool result)
   with `readings_summary()` as the fallback. Any new brain or report path
   must do the same.
3. **`data_source` / `mode: "mock"` reaches every output**: tool results, UI,
   prompts, and new outputs like trip summaries or spoken replies.
4. **Tool results carry value + unit + normal range**, and tools take zero
   arguments (small models pick names far better than they fill parameters).
5. **The dispatcher never raises.** New tools fail as structured errors.
6. **Read-only towards the car.** No clearing codes, no writes, no ENET coding.
7. **Driver-first copy:** what it means and what to do, two sentences when
   spoken.

## Testability

- Make it work under `--mock` first. If a feature needs a car state the
  scenarios can't produce, add a `MockSource` scenario (`SCENARIOS` in
  `source.py`) rather than testing only in the car.
- Extend `--selftest` (in `app.py`) when the feature is logic, not UI. It must
  stay runnable with **no third-party dependencies**, since CI runs it bare.

## Prove it

1. `python app.py --selftest`
2. `python app.py --mock --scenario <one that exercises it>`, checked in a
   browser (`car-ai-run`)
3. On the Pi against the car (`deploy-to-pi`), for anything that depends on
   real data, timing or hardware
4. `/doc-check`, then PR with "Verified" ticked honestly
