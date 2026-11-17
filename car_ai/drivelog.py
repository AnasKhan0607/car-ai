"""The drive log: one SQLite database that survives the car cutting power.

The car switches the Pi off by removing power, every single time. The CSV
log this replaced appended a line per poll to a file the OS might still be
holding in cache when the power went, so a drive could end in a truncated
row, or worse. Here:

* WAL mode with synchronous=FULL. Each poll is one small transaction, made
  durable when it commits. A power cut loses at most the poll in flight; the
  database itself is never left corrupt (SQLite replays or discards the WAL
  on the next open).
* Long format (one row per sensor per poll), so adding a sensor never needs
  a schema change, and later trip and trend queries are plain SQL.
* `drives.mode` records live vs simulated for every row. Simulated data must
  never pass for live data, in the log any more than on screen.

Stdlib only (sqlite3), so the self-test and CI exercise it with nothing
installed.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("car_ai.drivelog")

SCHEMA = """
CREATE TABLE IF NOT EXISTS drives (
    id          INTEGER PRIMARY KEY,
    started_at  REAL NOT NULL,          -- unix time
    mode        TEXT NOT NULL,          -- live | mock
    vehicle     TEXT                    -- CAR_AI_VEHICLE profile, if any
);
CREATE TABLE IF NOT EXISTS readings (
    drive_id    INTEGER NOT NULL REFERENCES drives(id),
    t           REAL NOT NULL,
    key         TEXT NOT NULL,          -- sensors.py key
    value       REAL,                   -- NULL when there was no value
    status      TEXT NOT NULL           -- ok | warn | crit | no_data | error
);
CREATE INDEX IF NOT EXISTS readings_drive_key_t ON readings(drive_id, key, t);
CREATE TABLE IF NOT EXISTS events (
    drive_id    INTEGER NOT NULL REFERENCES drives(id),
    t           REAL NOT NULL,
    kind        TEXT NOT NULL,          -- alert | dtc | connection
    key         TEXT,
    severity    TEXT,
    message     TEXT
);
"""


class DriveLog:
    """Append-only drive log. Every public method swallows its own errors:
    it is called from the poll thread, which must never die over logging."""

    def __init__(self, path: Path, mode: str, vehicle: str | None = None):
        self.path = Path(path)
        self.drive_id: int | None = None
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._logged_at: dict[str, float] = {}   # key -> updated_at last written
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, check_same_thread=False,
                                   isolation_level=None)   # explicit BEGIN/COMMIT
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            conn.executescript(SCHEMA)
            cur = conn.execute(
                "INSERT INTO drives (started_at, mode, vehicle) VALUES (?, ?, ?)",
                (time.time(), mode, vehicle))
            self.drive_id = cur.lastrowid
            self._conn = conn
            log.info("logging drive #%s to %s", self.drive_id, self.path)
        except Exception:
            log.exception("could not open drive log %s; continuing without it", self.path)

    def record(self, state, alerts: list[tuple[str, str]] | None = None) -> None:
        """One poll: every reading that is new since the last poll, in a single
        transaction, stamped with when it was actually read.

        Slower poll tiers carry the previous reading forward between reads;
        logging those again would store duplicates with the wrong time."""
        if self._conn is None:
            return
        rows = [(self.drive_id, r.updated_at, key, r.value, r.status)
                for key, r in state.readings.items()
                if r.status != "unsupported"    # "the car doesn't have it" is not news every 5 s
                and r.updated_at and r.updated_at != self._logged_at.get(key)]
        if not rows:
            return
        try:
            with self._lock:
                self._conn.execute("BEGIN")
                self._conn.executemany(
                    "INSERT INTO readings (drive_id, t, key, value, status) "
                    "VALUES (?, ?, ?, ?, ?)", rows)
                self._conn.execute("COMMIT")
            for _, at, key, _, _ in rows:
                self._logged_at[key] = at
        except Exception:
            self._rollback()
            log.exception("drive log write failed")

    def event(self, kind: str, key: str | None = None, severity: str | None = None,
              message: str | None = None) -> None:
        if self._conn is None:
            return
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO events (drive_id, t, kind, key, severity, message) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (self.drive_id, time.time(), kind, key, severity, message))
        except Exception:
            log.exception("drive log event failed")

    def close(self) -> None:
        if self._conn is not None:
            with self._lock:
                self._conn.close()
                self._conn = None

    def _rollback(self) -> None:
        try:
            with self._lock:
                if self._conn is not None and self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
        except Exception:
            pass


def integrity_ok(path: Path) -> bool:
    """True if the database at `path` opens and passes integrity_check."""
    try:
        conn = sqlite3.connect(path)
        try:
            return conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            conn.close()
    except Exception:
        return False
