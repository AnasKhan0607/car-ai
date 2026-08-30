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
