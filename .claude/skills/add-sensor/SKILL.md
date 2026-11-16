---
name: add-sensor
description: Add a new OBD-II sensor (PID) to the dashboard, alerts, and LLM tools. Use when asked to read, display, monitor, or alert on a vehicle value the app does not track yet — oil pressure, O2 sensors, boost, transmission temp, and so on.
---

# Adding a sensor

**One entry in `car_ai/sensors.py`. Do not edit other files.** The poller, the
dashboard tiles, the alert thresholds and the LLM tool schemas are all derived
from the `SENSORS` tuple. If you find yourself editing `source.py`,
`server.py` or a template to add a sensor, stop — you are working against the
design.

## The entry

```python
Sensor(
    key="oil_pressure",          # stable internal name; becomes get_oil_pressure
    command="OIL_PRESSURE",      # attribute on obd.commands
    label="Oil Pressure",        # what the tile prints
    unit="kPa",
    to_unit=None,                # pint conversion target, if needed
    decimals=0,
    tier=TIER_SECONDARY,         # PRIMARY (every cycle) / SECONDARY (3rd) / SLOW (6th)
    primary=False,               # True = large tile in the top row
    warn_low=100, crit_low=70,   # omit any threshold that doesn't apply
    context="Engine oil pressure. Low pressure at idle is normal; low "
            "pressure under load is serious and means stop driving.",
    aliases=(),                  # fallback obd.commands names
)
```

## Getting each field right

**`command` must exist in python-OBD.** Names differ across versions and some
are misspelled in the library (`AMBIANT_AIR_TEMP`). Check before assuming:

```bash
python -c "import obd; print([c for c in dir(obd.commands) if 'OIL' in c])"
```

If a name varies between versions, list the alternatives in `aliases` — the
resolver tries each and marks the sensor unsupported if none exist, rather
than crashing.

**`context` is load-bearing, not a comment.** It goes verbatim to a 3B model.
State what the value means, what normal looks like, and what abnormal implies.
Handed a bare number the model invents an interpretation; this is what stops
it. Write it for a driver, not an engineer.

**Thresholds are conservative and vehicle-generic.** Omit rather than guess —
a wrong threshold produces false alarms, and a dashboard that cries wolf gets
ignored at the moment it matters. `classify()` treats a `None` threshold as
"never trips".

**Tier by how fast it actually changes.** Each PID costs ~50–100 ms on an
ELM327. Only things a driver watches continuously belong in `TIER_PRIMARY`.

**`primary=True` is scarce.** Six large tiles is what fits a 7" in-car screen
at a glance. Adding a seventh means demoting another.

## Then

1. Add the fallback DTC description to `DTC_FALLBACK_DESCRIPTIONS` if the
   sensor relates to a specific code.
2. Add it to `MockSource._simulate` in `source.py` so the simulator produces a
   plausible value — **this is the one place outside `sensors.py` you touch.**
   Return `None` to simulate a PID the car doesn't implement.
3. If a fault scenario should move it, add an overlay in the scenario block.
4. `python app.py --selftest` — confirm the tool appears and reads.
5. `python app.py --mock` — confirm the tile renders.

## What NOT to do

- Don't give a missing reading a default value. Unavailable is a real, useful
  answer; a fabricated number is not. See the invariant in CLAUDE.md.
- Don't add a no-arg tool per secondary sensor. Only `primary` sensors get
  their own named tool; everything else is reachable via `get_all_readings()`.
  A 3B model picks reliably from ~10 tools, not 25.
