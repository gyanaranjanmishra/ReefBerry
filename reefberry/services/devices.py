"""Save discovery results to the database without overwriting the user's choices."""

from __future__ import annotations

import json

from ..db import Database, utcnow
from .kasa_service import FoundDevice, guess_role


def save_found_devices(db: Database, found: list[FoundDevice]) -> None:
    now = utcnow()
    with db.tx() as c:
        for d in found:
            # A device first seen without login has a temporary "host:<ip>" id.
            # Once we can read its real id, drop the placeholder.
            if not d.id.startswith("host:"):
                c.execute("DELETE FROM devices WHERE id = ?", (f"host:{d.host}",))
            c.execute(
                """
                INSERT INTO devices(id, host, model, alias, mac, firmware, conn, needs_auth, last_seen)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    host = excluded.host,
                    model = COALESCE(excluded.model, devices.model),
                    alias = COALESCE(excluded.alias, devices.alias),
                    mac = COALESCE(excluded.mac, devices.mac),
                    firmware = COALESCE(excluded.firmware, devices.firmware),
                    conn = COALESCE(excluded.conn, devices.conn),
                    needs_auth = excluded.needs_auth,
                    last_seen = excluded.last_seen
                """,
                (
                    d.id, d.host, d.model, d.alias, d.mac, d.firmware,
                    json.dumps(d.conn) if d.conn else None, int(d.needs_auth), now,
                ),
            )
            for o in d.outlets:
                existing = c.execute("SELECT id FROM outlets WHERE id = ?", (o.id,)).fetchone()
                if existing:
                    # Keep the user's display name, role and toggles; refresh what Kasa reports.
                    c.execute(
                        "UPDATE outlets SET device_id = ?, idx = ?, kasa_alias = ? WHERE id = ?",
                        (d.id, o.idx, o.alias, o.id),
                    )
                else:
                    order = c.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM outlets").fetchone()[0]
                    c.execute(
                        """
                        INSERT INTO outlets(id, device_id, idx, kasa_alias, display_name, role, sort_order)
                        VALUES(?, ?, ?, ?, ?, ?, ?)
                        """,
                        (o.id, d.id, o.idx, o.alias, o.alias, guess_role(o.alias), order),
                    )


def list_devices(db: Database) -> list[dict]:
    devices = []
    for row in db.query("SELECT * FROM devices ORDER BY host"):
        dev = dict(row)
        dev["conn"] = json.loads(dev["conn"]) if dev["conn"] else None
        dev["outlets"] = [
            dict(o) for o in db.query("SELECT * FROM outlets WHERE device_id = ? ORDER BY idx", (dev["id"],))
        ]
        devices.append(dev)
    return devices


def visible_outlets(db: Database) -> list[dict]:
    rows = db.query(
        """
        SELECT o.*, d.host, d.model, d.alias AS device_alias
        FROM outlets o JOIN devices d ON d.id = o.device_id
        WHERE o.hidden = 0 AND d.hidden = 0
        ORDER BY o.sort_order, d.host, o.idx
        """
    )
    return [dict(r) for r in rows]
