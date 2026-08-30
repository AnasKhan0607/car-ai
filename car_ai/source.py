"""The vehicle data source: a background poller that owns the OBD connection.

Design rules, each one a direct response to a way the previous version could
mislead the driver:

1. LIVE DATA AND SIMULATED DATA ARE NEVER MIXED. The old code fell through to
   a hardcoded number whenever a live read came back null, with nothing in the
   result to say so -- it would cheerfully report "72 km/h" parked in a
   driveway with no adapter attached. Here, mock mode is an explicit choice
   made once at startup (`--mock`), and in live mode a failed read reports
   itself as a failed read. A diagnostic tool that invents a plausible number
   is worse than one that admits it does not know.

2. ONE THREAD OWNS THE SERIAL PORT. An ELM327 cannot serve two clients, and
   requests are strictly one-at-a-time. A single poller holds the connection
   and publishes an immutable snapshot; the dashboard and the LLM both read
   that snapshot and never touch the port. That also means an LLM question
   never waits on serial I/O.

3. UNSUPPORTED IS NOT THE SAME AS BROKEN. At connect time we ask the car which
   PIDs it actually implements. Anything it does not implement is marked
   'unsupported' once and never polled again -- so a BMW that does not expose
   fuel level says exactly that, permanently, instead of looking like an
   intermittent fault.

4. READINGS CARRY AN AGE. Every value is timestamped. If the poller stalls or
   the adapter drops, consumers can see the data is stale rather than trusting
   a number frozen five minutes ago.
"""

from __future__ import annotations

import logging
import math
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .sensors import SENSORS, SENSORS_BY_KEY, Sensor, classify, describe_dtc

log = logging.getLogger("car_ai.source")

# A reading older than this is reported as stale rather than current.
STALE_AFTER_S = 20.0
# How long to wait before retrying a dropped connection.
RECONNECT_DELAY_S = 10.0


@dataclass
class Reading:
    """One sensor value plus everything needed to interpret or distrust it."""
    key: str
    label: str
    unit: str
    value: float | None = None
    status: str = "no_data"   # ok | warn | crit | unsupported | no_data | error
    detail: str = ""          # why, when there is no value
    updated_at: float = 0.0

    @property
    def age(self) -> float:
        return time.time() - self.updated_at if self.updated_at else float("inf")

    @property
    def stale(self) -> bool:
        return self.value is not None and self.age > STALE_AFTER_S

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "unit": self.unit,
            "value": self.value,
            "status": self.status,
            "detail": self.detail,
            "age": None if self.updated_at == 0.0 else round(self.age, 1),
            "stale": self.stale,
        }


@dataclass
class VehicleState:
    """An immutable-ish snapshot of everything we know right now."""
    readings: dict[str, Reading] = field(default_factory=dict)
    dtcs: list[dict[str, str]] = field(default_factory=list)
    mil_on: bool | None = None       # is the check-engine light commanded on
    dtc_read_at: float = 0.0
    mode: str = "live"               # live | mock
    connection_status: str = "starting"
    connection_detail: str = ""
    port: str | None = None
    protocol: str | None = None
    supported_count: int = 0
    poll_count: int = 0
    last_poll_at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "readings": {k: r.as_dict() for k, r in self.readings.items()},
            "dtcs": self.dtcs,
            "mil_on": self.mil_on,
            "dtc_read_at": self.dtc_read_at or None,
            "mode": self.mode,
            "connection": {
                "status": self.connection_status,
                "detail": self.connection_detail,
                "port": self.port,
                "protocol": self.protocol,
                "supported_count": self.supported_count,
            },
            "poll_count": self.poll_count,
            "last_poll_at": self.last_poll_at or None,
            "server_time": time.time(),
        }


class VehicleSource:
    """Base class. Subclasses implement `_connect` and `_read`."""

    mode = "live"

    def __init__(self, interval: float = 5.0):
        self.interval = interval
        self._lock = threading.Lock()
        self._state = VehicleState(mode=self.mode)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._supported: dict[str, bool] = {}
        self._listeners: list[Any] = []

    # -- public API ------------------------------------------------------
    def add_listener(self, fn) -> None:
        """Register fn(state) to be called after every poll cycle."""
        self._listeners.append(fn)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="obd-poller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)
        self._close()

    def snapshot(self) -> VehicleState:
        with self._lock:
            return self._state

    # -- the poll loop ---------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            if not self._is_connected():
                self._set(connection_status="connecting",
                          connection_detail="Looking for adapter...")
                try:
                    self._connect()
                except Exception as exc:              # never kill the thread
                    log.warning("connect failed: %s", exc)
                    self._set(connection_status="disconnected",
                              connection_detail=str(exc))
                    self._stop.wait(RECONNECT_DELAY_S)
                    continue

            started = time.time()
            try:
                self._poll_cycle()
            except Exception as exc:                  # a bad cycle is not fatal
                log.exception("poll cycle failed")
                self._set(connection_status="error", connection_detail=str(exc))

            for fn in self._listeners:
                try:
                    fn(self.snapshot())
                except Exception:
                    log.exception("listener failed")

            # Sleep the remainder of the interval, so a slow cycle does not
            # compound into ever-later polls.
            elapsed = time.time() - started
            self._stop.wait(max(0.5, self.interval - elapsed))

    def _poll_cycle(self) -> None:
        with self._lock:
            n = self._state.poll_count + 1

        readings: dict[str, Reading] = {}
        for sensor in SENSORS:
            if self._supported.get(sensor.key) is False:
                readings[sensor.key] = Reading(
                    key=sensor.key, label=sensor.label, unit=sensor.unit,
                    status="unsupported",
                    detail="This vehicle does not report this sensor.",
                    updated_at=time.time(),
                )
                continue
            # Respect the poll tier: reuse the previous reading on off-cycles.
            if n % sensor.tier != 0 and sensor.tier != 1:
                prev = self._state.readings.get(sensor.key)
                if prev is not None:
                    readings[sensor.key] = prev
                    continue
            readings[sensor.key] = self._read(sensor)

        updates: dict[str, Any] = {
            "readings": readings,
            "poll_count": n,
            "last_poll_at": time.time(),
            "connection_status": "connected",
            "connection_detail": "",
        }
        # Fault codes change rarely; read them on the slow tier only.
        if n == 1 or n % 6 == 0:
            try:
                dtcs, mil = self._read_dtcs()
                updates["dtcs"] = dtcs
                updates["mil_on"] = mil
                updates["dtc_read_at"] = time.time()
            except Exception as exc:
                log.warning("DTC read failed: %s", exc)

        self._set(**updates)

    def _set(self, **kwargs) -> None:
        with self._lock:
            for k, v in kwargs.items():
                setattr(self._state, k, v)

    # -- subclass hooks --------------------------------------------------
    def _is_connected(self) -> bool:
        raise NotImplementedError

    def _connect(self) -> None:
        raise NotImplementedError

    def _read(self, sensor: Sensor) -> Reading:
        raise NotImplementedError

    def _read_dtcs(self) -> tuple[list[dict[str, str]], bool | None]:
        raise NotImplementedError

    def _close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Live vehicle
# ---------------------------------------------------------------------------
class LiveOBDSource(VehicleSource):
    """Reads a real car through an ELM327 adapter via python-OBD."""

    mode = "live"

    def __init__(self, interval: float = 5.0, port: str | None = None,
                 baudrate: int | None = None, timeout: float = 1.0):
        super().__init__(interval)
        self._port = port
        self._baudrate = baudrate
        self._timeout = timeout
        self._conn = None
        self._commands: dict[str, Any] = {}

    def _is_connected(self) -> bool:
        try:
            return self._conn is not None and self._conn.is_connected()
        except Exception:
            return False

    def _connect(self) -> None:
        import obd

        # python-OBD is chatty on stdout; route it through logging instead.
        obd.logger.setLevel(obd.logging.WARNING)

        kwargs: dict[str, Any] = {"timeout": self._timeout}
        if self._port:
            kwargs["portstr"] = self._port
        if self._baudrate:
            kwargs["baudrate"] = self._baudrate

        conn = obd.OBD(**kwargs)
        if not conn.is_connected():
            status = conn.status()
            conn.close()
            raise ConnectionError(
                f"No OBD-II adapter responding ({status}). Check the adapter is "
                f"plugged into the car, the ignition is on, and you are in the "
                f"'dialout' group."
            )

        self._conn = conn
        self._resolve_commands(obd)

        supported = getattr(conn, "supported_commands", set()) or set()
        self._set(
            connection_status="connected",
            connection_detail="",
            port=str(getattr(conn, "port_name", lambda: None)()
                     if callable(getattr(conn, "port_name", None))
                     else getattr(conn, "port_name", None)),
            protocol=str(getattr(conn, "protocol_name", lambda: None)()
                         if callable(getattr(conn, "protocol_name", None))
                         else getattr(conn, "protocol_name", None)),
            supported_count=len(supported),
        )
        log.info("connected: %d PIDs supported", len(supported))

    def _resolve_commands(self, obd) -> None:
        """Map our sensor keys to real obd.commands objects, once.

        Command names differ across python-OBD versions (AMBIANT_AIR_TEMP is
        famously misspelled in the library), so every lookup is guarded and
        every alias tried before we give up on a sensor.
        """
        conn = self._conn
        supported = getattr(conn, "supported_commands", set()) or set()
        for sensor in SENSORS:
            cmd = None
            for name in (sensor.command, *sensor.aliases):
                cmd = getattr(obd.commands, name, None)
                if cmd is not None:
                    break
            if cmd is None:
                self._supported[sensor.key] = False
                log.info("%s: no such command in this python-OBD build", sensor.key)
                continue
            self._commands[sensor.key] = cmd
            # If the car published a supported-PID list, trust it.
            self._supported[sensor.key] = (cmd in supported) if supported else True
            if not self._supported[sensor.key]:
                log.info("%s: not supported by this vehicle", sensor.key)

    def _read(self, sensor: Sensor) -> Reading:
        cmd = self._commands.get(sensor.key)
        base = dict(key=sensor.key, label=sensor.label, unit=sensor.unit)
        if cmd is None:
            return Reading(**base, status="unsupported", updated_at=time.time(),
                           detail="This vehicle does not report this sensor.")
        try:
            resp = self._conn.query(cmd)
        except Exception as exc:
            return Reading(**base, status="error", updated_at=time.time(),
                           detail=f"Read failed: {exc}")

        if resp is None or resp.is_null():
            # Null is normal, not a bug: the engine may be off, or the ECU may
            # decline this PID right now. We report it as absent -- we do NOT
            # substitute a number.
            return Reading(**base, status="no_data", updated_at=time.time(),
                           detail="No response from the ECU for this sensor.")

        value = _to_number(resp.value, sensor)
        if value is None:
            return Reading(**base, status="error", updated_at=time.time(),
                           detail=f"Could not interpret response: {resp.value!r}")

        return Reading(**base, value=round(value, sensor.decimals) if sensor.decimals
                       else round(value), status=classify(sensor, value),
                       updated_at=time.time())

    def _read_dtcs(self) -> tuple[list[dict[str, str]], bool | None]:
        import obd

        codes: list[dict[str, str]] = []
        resp = self._conn.query(obd.commands.GET_DTC)
        if resp is not None and not resp.is_null() and resp.value:
            for entry in resp.value:
                # python-OBD returns (code, description). Keeping the library's
                # description matters: handing a 3B model a bare "P0128" and
                # asking it to explain the code invites a fluent hallucination.
                code = entry[0] if isinstance(entry, (list, tuple)) else str(entry)
                supplied = (entry[1] if isinstance(entry, (list, tuple))
                            and len(entry) > 1 else None)
                codes.append({"code": code, "description": describe_dtc(code, supplied)})

        mil = None
        status_cmd = getattr(obd.commands, "STATUS", None)
        if status_cmd is not None:
            try:
                sresp = self._conn.query(status_cmd)
                if sresp is not None and not sresp.is_null() and sresp.value:
                    mil = bool(getattr(sresp.value, "MIL", False))
            except Exception:
                pass
        return codes, mil

    def _close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None


def _to_number(value: Any, sensor: Sensor) -> float | None:
    """Coerce a python-OBD value (usually a pint Quantity) to a plain float."""
    try:
        if sensor.to_unit is not None and hasattr(value, "to"):
            try:
                value = value.to(sensor.to_unit)
            except Exception:
                pass  # already in the right unit, or not convertible
        if hasattr(value, "magnitude"):
            return float(value.magnitude)
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Simulator
# ---------------------------------------------------------------------------
class MockSource(VehicleSource):
    """A plausible fake car, for development on a desk.

    This exists so the dashboard, the alert logic, and the assistant can all be
    exercised without a vehicle -- including the failure states, which is the
    part you cannot test on a healthy car. Scenarios drive the interesting
    branches: you cannot verify that the assistant correctly says "pull over"
    if the simulated coolant is always a comfortable 91 C.

    It is reachable only via an explicit `--mock` flag, and everything it
    produces is labelled mode="mock" all the way to the screen.
    """

    mode = "mock"

    SCENARIOS = ("normal", "overheat", "low_fuel", "misfire", "charging_fault")

    def __init__(self, interval: float = 5.0, scenario: str = "normal"):
        super().__init__(interval)
        if scenario not in self.SCENARIOS:
            raise ValueError(f"unknown scenario {scenario!r}; "
                             f"choose from {', '.join(self.SCENARIOS)}")
        self.scenario = scenario
        self._t0 = time.time()

    def _is_connected(self) -> bool:
        return True

    def _connect(self) -> None:
        self._set(connection_status="connected",
                  connection_detail=f"Simulated vehicle ({self.scenario})",
                  port="mock", protocol="simulated",
                  supported_count=len(SENSORS))

    def _read(self, sensor: Sensor) -> Reading:
        t = time.time() - self._t0
        value = self._simulate(sensor.key, t)
        base = dict(key=sensor.key, label=sensor.label, unit=sensor.unit)
        if value is None:
            # Simulate the real-world case that bit us: a PID the car does not
            # implement. On a BMW, fuel level really does behave like this.
            return Reading(**base, status="unsupported", updated_at=time.time(),
                           detail="This vehicle does not report this sensor.")
        return Reading(**base,
                       value=round(value, sensor.decimals) if sensor.decimals
                       else round(value),
                       status=classify(sensor, value), updated_at=time.time())

    def _simulate(self, key: str, t: float) -> float | None:
        wave = math.sin(t / 25.0)
        jitter = random.uniform(-1, 1)

        if key == "fuel_level" and self.scenario != "low_fuel":
            return None  # mimic a car that does not expose the PID at all

        base: dict[str, float | None] = {
            "speed": max(0.0, 62 + wave * 28 + jitter * 2),
            "rpm": max(700.0, 2100 + wave * 700 + jitter * 40),
            "coolant_temp": 92 + wave * 3 + jitter * 0.5,
            "engine_load": max(4.0, 38 + wave * 22 + jitter * 2),
            "fuel_level": 46 + wave,
            "voltage": 14.1 + wave * 0.2 + jitter * 0.05,
            "intake_temp": 31 + wave * 4,
            "throttle": max(12.0, 22 + wave * 14),
            "oil_temp": 104 + wave * 5,
            "ambient_temp": 19 + wave,
            "maf": max(2.0, 9 + wave * 5),
            "intake_pressure": 42 + wave * 20,
            "timing_advance": 12 + wave * 6,
            "short_fuel_trim": wave * 3 + jitter,
            "long_fuel_trim": 2 + wave,
            "run_time": t,
            "fuel_rate": max(0.5, 7 + wave * 4),
        }.copy()

        # Scenario overlays -- the whole point of the simulator.
        if self.scenario == "overheat":
            # A steady climb, so you can watch the alert cross warn then crit.
            base["coolant_temp"] = 96 + min(t / 12.0, 26)
            base["oil_temp"] = 118 + min(t / 20.0, 25)
        elif self.scenario == "low_fuel":
            base["fuel_level"] = max(3.0, 14 - t / 90.0)
        elif self.scenario == "misfire":
            base["rpm"] = 1500 + wave * 900 + random.uniform(-180, 180)
            base["short_fuel_trim"] = 18 + jitter * 4
            base["long_fuel_trim"] = 21 + jitter * 2
        elif self.scenario == "charging_fault":
            base["voltage"] = max(10.9, 13.9 - t / 45.0)

        return base.get(key)

    def _read_dtcs(self) -> tuple[list[dict[str, str]], bool | None]:
        codes = {
            "normal": [],
            "overheat": ["P0217"],
            "low_fuel": [],
            "misfire": ["P0301", "P0171"],
            "charging_fault": [],
        }[self.scenario]
        return (
            [{"code": c, "description": describe_dtc(c)} for c in codes],
            bool(codes),
        )


def build_source(mock: bool = False, scenario: str = "normal",
                 interval: float = 5.0, port: str | None = None,
                 baudrate: int | None = None) -> VehicleSource:
    if mock:
        return MockSource(interval=interval, scenario=scenario)
    return LiveOBDSource(interval=interval, port=port, baudrate=baudrate)
