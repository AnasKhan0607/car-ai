# Build phases

The build plan for Car AI on the 2020 BMW 430i Gran Coupe (F36, B46 2.0 L
turbo). It merges the original dashboard work with the owner's build spec
(Claude as the brain, voice, trips, power safety, ENET).

**Rules for every phase**

- Every task is a GitHub issue on `AnasKhan0607/car-ai`, worked on an
  `issue-N-slug` branch and merged by PR (see the `project-management` skill).
- A phase is done when its *done-when* line has been **seen working on the Pi
  in the car**, not when the code is written. The README status table is
  updated in the same PR that proves it.
- Read-only towards the car, always. Nothing writes, codes or clears modules.
- The no-mixing rule in `CLAUDE.md` (live and simulated data never mixed)
  applies to every new feature, including anything the AI says.

---

## Phase 0: Foundation ✅

Live dashboard, alert engine, simulator scenarios, LLM tool layer, CSV drive
log, start-on-boot kiosk on the 7" touchscreen.

Done 2026-11-15: live reads confirmed on the 430i (parked, idling), kiosk boots
fullscreen after a cold reboot, touch works.

## Phase 1: Trust the car's numbers

The dashboard runs live but has only been seen parked. Make what it says
right for *this* car before anything is built on top of it.

- ~~Per-vehicle thresholds~~ (#1): `bmw_b46` profile raises coolant to warn
  115 / crit 120 °C. Oil (~111 °C, warn 125) needed no change; recheck while
  driving (#3).
- ~~List the supported PIDs and add the useful ones~~ (#2): the list is in
  `docs/vehicle-430i-pids.md` (39 live readings, not 111); added catalyst
  temp, fuel rail pressure, barometer, distance since codes cleared, and
  distance with the engine light on.
- Verify live reads **while driving**: speed/RPM respond, no stale tiles,
  measure real poll latency per tier and tune the interval (the spec wants
  1–2 s for the core values; ~50–100 ms per PID makes that feasible only for
  the primary tier).
- Verify the 800×480 layout is readable at a glance from the driver's seat.
- Dashboard reachable from a phone on the hotspot (it is IPv6-only, and the
  server currently binds IPv4 `0.0.0.0`).

**Done when:** a real drive shows correct, non-alarming readings for a healthy
car, and the README rows for driving and layout are ✅.

## Phase 2: The brain

- ~~Run the Ollama chat loop end-to-end~~ (#6): runs, live and simulated.
  Guards added in code (unreadable topics, invented-number check, simulated
  label). Still slow (25–65 s) and can misattribute readings: the baseline
  #7 is measured against with `scripts/chat_check.py`.
- Add a `Brain` in front of the assistant: Claude Code headless first
  (`claude -p ... --output-format json`, ~30 s timeout), then Ollama, then a
  short canned reply. Detect usage-limit errors and back off until reset.
- Claude gets a compact summary (readings + units + normal ranges, trend,
  active DTCs, trip so far), never the raw stream. `data_source` stays on
  everything so a simulated session is still labelled as simulated.
- System prompt file describing the car and keeping spoken answers short.
- ~~Check Anthropic's terms for personal headless use~~ (#9): allowed for
  the owner's own use; see `docs/claude-terms.md` for the constraints #7 must
  follow (clean working dir, API-key path too, event-driven only).

**Done when:** in the car, a question gets a Claude answer online and an Ollama
answer with the hotspot off, and both refuse to invent a missing reading.

## Phase 3: Survive power loss

The car cuts power with no shutdown every time it is switched off.

- Move the drive log from CSV to SQLite in WAL mode, small batched writes.
- Writable data partition (or USB stick) for logs and the database.
- Overlay (read-only) root filesystem. Design the deploy path first: with the
  overlay on, `git pull` on the Pi no longer persists.
- Every service tolerant of starting without internet, adapter or ignition.

**Done when:** pulling power mid-drive 10 times in a row loses at most a few
seconds of data and boots cleanly every time.

## Phase 4: Voice

Hands-free while driving: wake word (openWakeWord), speech-to-text
(whisper.cpp or Vosk), Piper for speech, replies of two sentences or fewer.
Needs a USB mic and a speaker or car audio path. **Ask before buying or
assuming hardware.**

**Done when:** a spoken question in the moving car gets a spoken answer
without touching the screen.

## Phase 5: Trips and reports

Trip detection (ignition on → moving → parked a few minutes), per-trip stats,
one Claude summary per trip, driving smoothness score with personal best, fuel
cost per trip, weekly health trends (battery, coolant, economy).

## Phase 6: Personality

Toggleable first-person car persona and roast mode, kept short and never
distracting; 0–100 km/h timer from speed data.

## Phase 7: Deep data

BMW ENET cable (HSFZ on port 6801 carrying UDS, e.g. `udsoncan` with a custom
transport) for modules the ELM327 can't reach: transmission, battery
management, service intervals, BMW-specific fault codes. Experimental and
strictly read-only. Optional dashcam (Pi Camera 3) and GPS.
