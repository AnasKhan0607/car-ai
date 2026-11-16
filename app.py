#!/usr/bin/env python3
"""Car AI — entry point.

    python app.py                       # live car, dashboard on :5000
    python app.py --mock                # simulated car, no adapter needed
    python app.py --mock --scenario overheat
    python app.py --selftest            # exercise the tool layer, no model
    python app.py --kiosk               # also open Chromium fullscreen

The dashboard polls the car every 5 seconds and answers questions about it.
Everything runs on the device: no cloud, no network dependency beyond the
local browser.
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import subprocess
import sys
import time

from car_ai.assistant import DEFAULT_MODEL, Assistant
from car_ai.monitor import Monitor
from car_ai.sensors import (GENERIC_SENSORS, VEHICLE_PROFILE, VEHICLE_PROFILES,
                             apply_profile)
from car_ai.source import MockSource, build_source
from car_ai.tools import build_registry, dispatch


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Car AI — local OBD-II dashboard and diagnostic assistant.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    src = p.add_argument_group("data source")
    src.add_argument("--mock", action="store_true",
                     help="Use simulated vehicle data instead of a real car. "
                          "Live and simulated data are never mixed: without "
                          "this flag, a failed read is reported as a failed "
                          "read, never replaced with a plausible number.")
    src.add_argument("--scenario", default="normal",
                     choices=list(MockSource.SCENARIOS),
                     help="Which fault to simulate in --mock mode (default: normal).")
    src.add_argument("--serial-port", default=None,
                     help="Serial device of the ELM327, e.g. /dev/ttyUSB0. "
                          "Omit to autoscan.")
    src.add_argument("--baudrate", type=int, default=None,
                     help="Override the adapter baud rate (e.g. 38400).")
    src.add_argument("--interval", type=float, default=5.0,
                     help="Seconds between OBD poll cycles (default: 5).")

    web = p.add_argument_group("web server")
    web.add_argument("--host", default="0.0.0.0",
                     help="Bind address. Default 0.0.0.0 so you can open the "
                          "dashboard from another machine on the same network; "
                          "use 127.0.0.1 to restrict it to the Pi.")
    web.add_argument("--http-port", type=int, default=5000, help="HTTP port (default: 5000).")
    web.add_argument("--kiosk", action="store_true",
                     help="Launch Chromium fullscreen on the Pi's display.")

    ai = p.add_argument_group("assistant")
    ai.add_argument("--model", default=DEFAULT_MODEL,
                    help=f"Ollama model to use (default: {DEFAULT_MODEL}).")

    p.add_argument("--no-log", action="store_true",
                   help="Do not write a CSV drive log.")
    p.add_argument("--log-dir", default=None, help="Where to write drive logs.")
    p.add_argument("--selftest", action="store_true",
                   help="Exercise the tool layer against the simulator and exit. "
                        "Needs no adapter, no Ollama, and no network.")
    p.add_argument("-v", "--verbose", action="store_true", help="Debug logging.")
    return p.parse_args(argv)


def self_test() -> int:
    """Prove the tool layer works end to end without a model or a car."""
    print("Car AI — tool-layer self-test (no model, no adapter)\n")
    source = MockSource(interval=0.2, scenario="overheat")
    source._connect()
    # Wind the simulated clock forward so the overheat ramp has actually
    # crossed the alert thresholds -- a self-test that only ever sees
    # healthy numbers proves nothing about the alerting.
    source._t0 = time.time() - 300
    source._poll_cycle()
    source.monitor = Monitor(enable_logging=False)
    source.monitor.on_snapshot(source.snapshot())
    source.monitor.on_snapshot(source.snapshot())  # clear the debounce

    registry, schemas = build_registry(source)
    for name in sorted(registry):
        result = dispatch(registry, name)
        print(f"  {name}()")
        print(f"    {json.dumps(result, default=str)[:300]}")

    print("\n  Hallucinated tool name:")
    print(f"    {json.dumps(dispatch(registry, 'get_tire_pressure'))}")

    print(f"\n  {len(schemas)} tool schemas generated.")
    alerts = source.monitor.active_alerts()
    print(f"  {len(alerts)} alert(s) raised by the overheat scenario:")
    for a in alerts:
        print(f"    [{a['severity']}] {a['title']}: {a['message']}")

    # Vehicle profiles are pure data, so check every one regardless of which
    # (if any) this process loaded: a profile must actually move a threshold,
    # keep its warn below its crit, and come with its own context text.
    print("\n  Vehicle profiles:")
    for name, overrides in sorted(VEHICLE_PROFILES.items()):
        profiled = {s.key: s for s in apply_profile(GENERIC_SENSORS, name)}
        for key, fields in overrides.items():
            s, g = profiled[key], next(x for x in GENERIC_SENSORS if x.key == key)
            assert "context" in fields, f"{name}/{key}: override context too"
            for lo, hi in (("warn_high", "crit_high"), ("crit_low", "warn_low")):
                a, b = getattr(s, lo), getattr(s, hi)
                assert a is None or b is None or a < b, f"{name}/{key}: {lo} >= {hi}"
            moved = [f for f in fields if f != "context" and getattr(s, f) != getattr(g, f)]
            print(f"    {name}: {key} {', '.join(f'{f} {getattr(g, f)}->{getattr(s, f)}' for f in moved)}")
    print(f"  Loaded profile: {VEHICLE_PROFILE or 'generic'}")

    # Staleness follows each sensor's own poll tier: a slow-tier value must not
    # read as stale between its normal refreshes, yet must once it misses one.
    from car_ai.sensors import SENSORS_BY_KEY, TIER_PRIMARY, TIER_SLOW
    from car_ai.source import Reading
    timed = MockSource(interval=5.0)
    for tier, fresh, missed in ((TIER_PRIMARY, 15, 25), (TIER_SLOW, 35, 65)):
        sensor = next(s for s in SENSORS_BY_KEY.values() if s.tier == tier)
        limit = timed._stale_after(sensor)
        for age, want in ((fresh, False), (missed, True)):
            r = Reading(key="t", label="t", unit="", value=1.0,
                        updated_at=time.time() - age, stale_after=limit)
            assert r.stale is want, f"tier {tier}: {age}s old stale={r.stale}"
    print("  Staleness per poll tier: ok")

    # History trimming must never drop the question being answered: when it
    # did, the model got a page of readings with no question and invented one.
    from car_ai.assistant import HISTORY_CHAR_BUDGET, Assistant
    a = Assistant(source)
    a._messages = [
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": "x" * HISTORY_CHAR_BUDGET},
        {"role": "user", "content": "current question"},
        {"role": "assistant", "content": ""},
        {"role": "tool", "content": "y" * (HISTORY_CHAR_BUDGET + 1)},
    ]
    a._trim()
    assert a._messages[0]["content"] == "current question", a._messages[0]
    all_readings = json.dumps(dispatch(registry, "get_all_readings"))
    assert len(all_readings) < HISTORY_CHAR_BUDGET // 2, len(all_readings)
    print(f"  History trim keeps the current question; get_all_readings is "
          f"{len(all_readings)} chars: ok")

    # Questions about things OBD-II never reports get a fixed answer in code;
    # nearby readings that ARE on OBD must still reach the model.
    from car_ai.assistant import not_on_obd
    for q, want in (("What's my tyre pressure?", "tyre pressure"),
                    ("are my tires ok", "tyre pressure"),
                    ("Is my oil level low?", "oil level"),
                    ("do I have enough coolant", "coolant level"),
                    ("What's my oil temperature?", None),
                    ("What's my coolant temperature?", None),
                    ("How is the car doing?", None),
                    ("Am I tired of driving?", None)):
        assert not_on_obd(q) == want, (q, not_on_obd(q))
    print("  Unreadable-topic guard: ok")

    # Numbers in an answer must come from the tools; an invented one gets the
    # answer replaced with a code-written summary that is still labelled
    # simulated when it is.
    from car_ai.assistant import invented_numbers, readings_summary
    trace = [{"tool": "get_speed", "result": dispatch(registry, "get_speed")},
             {"tool": "get_all_readings", "result": dispatch(registry, "get_all_readings")}]
    speed = trace[0]["result"]["value"]
    assert not invented_numbers(f"You're doing {speed} km/h.", trace)
    assert invented_numbers("You're doing 999 km/h.", trace) == {999.0}
    rpm = next(v["value"] for k, v in trace[1]["result"]["readings"].items() if k == "rpm")
    assert not invented_numbers(f"Engine at {rpm} rpm, normal is below 4500 rpm.", trace)
    assert invented_numbers(f"A moderate speed of {rpm} km/h.", trace) == {float(rpm)}
    summary = readings_summary(trace)
    assert summary.startswith("SIMULATED") and f"Speed: {speed} km/h" in summary, summary
    everything = dispatch(registry, "get_all_readings")
    assert everything["fault_codes"] and everything["active_alerts"], everything
    print("  Invented-number check and fallback summary: ok")
    print("\nSelf-test complete — tool layer, alerting and schemas all OK.")
    return 0


def launch_kiosk(url: str) -> None:
    """Open Chromium fullscreen at the dashboard. Best-effort."""
    for binary in ("chromium-browser", "chromium", "chromium-browser-v7"):
        try:
            subprocess.Popen(
                [binary, "--kiosk", "--noerrdialogs", "--disable-infobars",
                 "--incognito", "--check-for-update-interval=31536000", url],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            print(f"  kiosk: launched {binary}")
            return
        except FileNotFoundError:
            continue
    print("  kiosk: Chromium not found — open the URL manually.")


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    if args.selftest:
        return self_test()

    source = build_source(mock=args.mock, scenario=args.scenario,
                          interval=args.interval, port=args.serial_port,
                          baudrate=args.baudrate)
    monitor = Monitor(log_dir=args.log_dir, enable_logging=not args.no_log)
    # The tool layer reaches the monitor through the source, so the assistant
    # can answer "is anything wrong?" from the same alerts shown on screen.
    source.monitor = monitor
    source.add_listener(monitor.on_snapshot)

    assistant = Assistant(source, model=args.model)

    from car_ai.server import create_app
    app = create_app(source, monitor, assistant)

    source.start()

    url = f"http://localhost:{args.http_port}"
    print("\n" + "=" * 58)
    print("  CAR AI")
    print("=" * 58)
    print(f"  data source : {'SIMULATED (' + args.scenario + ')' if args.mock else 'live vehicle via ELM327'}")
    print(f"  vehicle     : {VEHICLE_PROFILE or 'generic thresholds'}")
    print(f"  poll every  : {args.interval:g}s")
    print(f"  model       : {args.model}")
    if monitor.log_path:
        print(f"  drive log   : {monitor.log_path}")
    print(f"  dashboard   : {url}")
    if args.host == "0.0.0.0":
        print(f"                (also reachable on your LAN — use --host 127.0.0.1 to restrict)")
    print("=" * 58 + "\n")

    if args.kiosk:
        time.sleep(1.5)  # let the server bind before the browser asks for it
        launch_kiosk(url)

    def shutdown(signum, frame):
        print("\nStopping…")
        source.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        # threaded=True matters: a model answer can occupy a request for many
        # seconds, and the dashboard must keep refreshing meanwhile.
        app.run(host=args.host, port=args.http_port, threaded=True,
                debug=False, use_reloader=False)
    finally:
        source.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
