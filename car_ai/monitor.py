"""Alerting and drive logging -- the part that watches when nobody is asking.

A chat assistant only ever looks at the car when spoken to. Coolant creeping
upward over ten minutes is exactly the thing a driver will not think to ask
about until it is a repair bill, so the monitor runs off the same 5-second
poll and raises the alarm on its own.

Two behaviours worth knowing:

* Alerts are DEBOUNCED. A single out-of-range sample does not trigger anything;
  a reading has to stay bad for `debounce` consecutive cycles. ELM327 adapters
  return the occasional garbage value, and a dashboard that cries wolf at
  70 km/h gets ignored at the moment it matters.

* Alerts CLEAR on their own once the reading returns to normal, but a fired
  alert is always written to the log even if it clears, so a transient spike
  is still there when you review the drive afterwards.
"""

from __future__ import annotations

import csv
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .sensors import SENSORS, SENSORS_BY_KEY, normal_range_text

log = logging.getLogger("car_ai.monitor")

DEFAULT_LOG_DIR = Path.home() / "car_ai_logs"


@dataclass
class Alert:
    key: str                 # sensor key, or a synthetic one like 'dtc'
    severity: str            # warn | crit
    title: str
    message: str
    since: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "severity": self.severity,
            "title": self.title,
            "message": self.message,
            "since": self.since,
            "age": round(time.time() - self.since, 1),
        }


class Monitor:
    """Evaluates each snapshot for trouble and appends it to a drive log."""

    def __init__(self, log_dir: Path | str | None = None, debounce: int = 2,
                 enable_logging: bool = True):
        self.debounce = max(1, debounce)
        self._lock = threading.Lock()
        self._alerts: dict[str, Alert] = {}
        self._strikes: dict[str, int] = {}
        self._known_dtcs: set[str] | None = None
        self._history: list[dict[str, Any]] = []   # fired alerts, for review
        self._csv_path: Path | None = None
        self._csv_fields: list[str] = []
        if enable_logging:
            # --log-dir arrives as a str from argparse; coerce it here so the
            # only place that touches the filesystem always has a Path.
            self._open_log(Path(log_dir) if log_dir else DEFAULT_LOG_DIR)

    # -- public ----------------------------------------------------------
    @property
    def log_path(self) -> Path | None:
        return self._csv_path

    def active_alerts(self) -> list[dict[str, Any]]:
        with self._lock:
            alerts = sorted(
                self._alerts.values(),
                key=lambda a: (0 if a.severity == "crit" else 1, a.since),
            )
            return [a.as_dict() for a in alerts]

    def alert_history(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return self._history[-limit:]

    def on_snapshot(self, state) -> None:
        """Poller listener. Must never raise -- it runs on the poll thread."""
        try:
            self._evaluate(state)
        except Exception:
            log.exception("alert evaluation failed")
        try:
            self._append_log(state)
        except Exception:
            log.exception("drive log append failed")

    # -- alerting --------------------------------------------------------
    def _evaluate(self, state) -> None:
        seen: set[str] = set()

        for sensor in SENSORS:
            reading = state.readings.get(sensor.key)
            if reading is None or reading.value is None:
                self._strikes.pop(sensor.key, None)
                continue
            if reading.status in ("warn", "crit"):
                seen.add(sensor.key)
                self._strike(sensor.key, reading, sensor)
            else:
                self._clear(sensor.key)

        # A new fault code is always worth surfacing immediately -- no debounce,
        # because the ECU has already done the debouncing for us.
        codes = {d["code"] for d in state.dtcs}
        if self._known_dtcs is None:
            self._known_dtcs = codes
            if codes:
                self._raise(Alert(
                    key="dtc", severity="warn",
                    title=f"{len(codes)} stored fault code"
                          f"{'s' if len(codes) != 1 else ''}",
                    message="; ".join(
                        f"{d['code']}: {d['description']}" for d in state.dtcs),
                ))
                seen.add("dtc")
            else:
                self._clear("dtc")
        else:
            new = codes - self._known_dtcs
            self._known_dtcs = codes
            if new:
                descriptions = {d["code"]: d["description"] for d in state.dtcs}
                self._raise(Alert(
                    key="dtc", severity="crit",
                    title="New fault code detected",
                    message="; ".join(
                        f"{c}: {descriptions.get(c, 'unknown')}" for c in sorted(new)),
                ))
            if codes:
                seen.add("dtc")
            else:
                self._clear("dtc")

        # Connection trouble is itself an alert -- otherwise a dropped adapter
        # just looks like a dashboard full of frozen numbers.
        if state.connection_status in ("disconnected", "error"):
            self._raise(Alert(
                key="connection", severity="warn",
                title="Lost connection to the car",
                message=state.connection_detail or
                        "The OBD adapter stopped responding. Readings below are "
                        "not current.",
            ))
            seen.add("connection")
        else:
            self._clear("connection")

        with self._lock:
            for key in list(self._alerts):
                if key not in seen:
                    self._alerts.pop(key, None)

    def _strike(self, key: str, reading, sensor) -> None:
        n = self._strikes.get(key, 0) + 1
        self._strikes[key] = n
        if n < self.debounce:
            return
        band = normal_range_text(sensor)
        direction = "high" if (
            sensor.warn_high is not None and reading.value >= sensor.warn_high
        ) else "low"
        headline = ("critical" if reading.status == "crit" else "unusual")
        msg = f"{sensor.label} is {reading.value}{sensor.unit} -- {headline} ({direction})."
        if band:
            msg += f" Normal is {band}."
        if key == "coolant_temp" and reading.status == "crit":
            msg += " Pull over safely and let the engine cool."
        self._raise(Alert(key=key, severity=reading.status,
                          title=f"{sensor.label} {direction}", message=msg))

    def _raise(self, alert: Alert) -> None:
        with self._lock:
            existing = self._alerts.get(alert.key)
            if existing and existing.severity == alert.severity:
                existing.message = alert.message   # refresh the number
                return
            self._alerts[alert.key] = alert
            self._history.append({**alert.as_dict(),
                                  "at": time.strftime("%Y-%m-%d %H:%M:%S")})
        log.warning("ALERT [%s] %s: %s", alert.severity, alert.title, alert.message)

    def _clear(self, key: str) -> None:
        self._strikes.pop(key, None)
        with self._lock:
            self._alerts.pop(key, None)

    # -- drive log -------------------------------------------------------
    def _open_log(self, log_dir: Path) -> None:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            self._csv_path = log_dir / f"drive_{stamp}.csv"
            self._csv_fields = (["timestamp", "mode", "connection"]
                                + [s.key for s in SENSORS]
                                + ["dtcs", "alerts"])
            with self._csv_path.open("w", newline="") as fh:
                csv.writer(fh).writerow(self._csv_fields)
            log.info("logging drive to %s", self._csv_path)
        except Exception:
            log.exception("could not open drive log; continuing without it")
            self._csv_path = None

    def _append_log(self, state) -> None:
        if self._csv_path is None:
            return
        row = [time.strftime("%Y-%m-%d %H:%M:%S"), state.mode,
               state.connection_status]
        for sensor in SENSORS:
            reading = state.readings.get(sensor.key)
            row.append("" if reading is None or reading.value is None
                       else reading.value)
        row.append(" ".join(d["code"] for d in state.dtcs))
        with self._lock:
            row.append(" ".join(f"{a.severity}:{a.key}"
                                for a in self._alerts.values()))
        with self._csv_path.open("a", newline="") as fh:
            csv.writer(fh).writerow(row)
