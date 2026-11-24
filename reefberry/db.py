"""SQLite storage with simple numbered migrations (PRAGMA user_version)."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

MIGRATIONS: list[str] = [
    # 1: users, settings, Kasa devices and outlets, event log
    """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        password_hash TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE devices (
        id TEXT PRIMARY KEY,            -- Kasa device id (stable across IP changes)
        host TEXT NOT NULL,
        model TEXT,
        alias TEXT,
        mac TEXT,
        firmware TEXT,
        conn TEXT,                      -- saved connection parameters (JSON, no credentials)
        needs_auth INTEGER NOT NULL DEFAULT 0,
        hidden INTEGER NOT NULL DEFAULT 0,
        last_seen TEXT
    );
    CREATE TABLE outlets (
        id TEXT PRIMARY KEY,            -- child id for strips, device id for single plugs
        device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
        idx INTEGER NOT NULL,           -- position on the strip (0 for single plugs)
        kasa_alias TEXT,
        display_name TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'other',
        hidden INTEGER NOT NULL DEFAULT 0,
        alert_enabled INTEGER NOT NULL DEFAULT 1,
        sort_order INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE events (
        id INTEGER PRIMARY KEY,
        ts TEXT NOT NULL,
        kind TEXT NOT NULL,
        outlet_id TEXT,
        detail TEXT
    );
    CREATE INDEX events_ts ON events(ts);
    """,
    # 2: bump on password change/reset so existing sign-ins stop working
    """
    ALTER TABLE users ADD COLUMN session_epoch INTEGER NOT NULL DEFAULT 0;
    """,
    # 3: optional per-outlet icon (NULL = use the icon for the outlet's equipment type)
    """
    ALTER TABLE outlets ADD COLUMN icon TEXT;
    """,
    # 4: automation state that must survive a restart
    #    outlet_status.held_by: who switched the outlet off and will bring it back
    #    ('rule', 'feed', 'water_change'); NULL means nobody will switch it back on.
    #    timers: pending "turn back on" steps (rule delay, feed end, water change restart).
    """
    CREATE TABLE outlet_status (
        outlet_id TEXT PRIMARY KEY REFERENCES outlets(id) ON DELETE CASCADE,
        is_on INTEGER,
        off_since REAL,
        alerted INTEGER NOT NULL DEFAULT 0,
        held_by TEXT
    );
    CREATE TABLE timers (
        id INTEGER PRIMARY KEY,
        outlet_id TEXT NOT NULL REFERENCES outlets(id) ON DELETE CASCADE,
        due_at REAL NOT NULL,
        owner TEXT NOT NULL,
        reason TEXT NOT NULL
    );
    """,
]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        self.path = path
        # Re-entrant: helpers like query() may run inside an open tx() on the same thread.
        self._lock = threading.RLock()
        new_file = not path.exists()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        if new_file:
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        self._migrate()

    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        for number, script in enumerate(MIGRATIONS, start=1):
            if number <= version:
                continue
            with self._lock:
                self._conn.execute("BEGIN")
                try:
                    for statement in script.split(";"):
                        if statement.strip():
                            self._conn.execute(statement)
                    self._conn.execute(f"PRAGMA user_version = {number}")
                    self._conn.execute("COMMIT")
                except Exception:
                    self._conn.execute("ROLLBACK")
                    raise

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def execute(self, sql: str, params: tuple | dict = ()) -> None:
        with self._lock:
            self._conn.execute(sql, params)

    def close(self) -> None:
        self._conn.close()

    # ---- settings (JSON values) ----
    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM settings WHERE key = ?", (key,))
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )

    def set_settings(self, values: dict[str, Any]) -> None:
        with self.tx() as c:
            for key, value in values.items():
                c.execute(
                    "INSERT INTO settings(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, json.dumps(value)),
                )

    def log_event(self, kind: str, outlet_id: str | None = None, detail: str = "") -> None:
        self.execute(
            "INSERT INTO events(ts, kind, outlet_id, detail) VALUES(?, ?, ?, ?)",
            (utcnow(), kind, outlet_id, detail),
        )
