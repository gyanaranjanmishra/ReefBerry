"""Water tests: the parameters you measure, their green bands, and the readings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ..db import Database

# Values outside these are almost certainly typing mistakes (e.g. 4100 ppm calcium).
PLAUSIBLE = {
    "alk": (0, 25),
    "ca": (0, 800),
    "mg": (0, 2500),
    "no3": (0, 300),
    "po4": (0, 10),
}
TIME_FORMAT = "%Y-%m-%dT%H:%M"
MAX_NOTE = 200


class ParamError(ValueError):
    """A problem with what was entered, worded for the person entering it."""


@dataclass
class Param:
    key: str
    name: str
    unit: str
    low: float | None
    high: float | None
    decimals: int

    def fmt(self, value: float | None) -> str:
        if value is None:
            return "–"
        return f"{value:.{self.decimals}f}"

    def status(self, value: float | None) -> str | None:
        """'low', 'ok' or 'high' against the green band; None when there's nothing to compare."""
        if value is None:
            return None
        if self.low is not None and value < self.low:
            return "low"
        if self.high is not None and value > self.high:
            return "high"
        return "ok"

    @property
    def band_text(self) -> str:
        if self.low is None and self.high is None:
            return "No green band set"
        if self.low is None:
            return f"Up to {self.fmt(self.high)} {self.unit}"
        if self.high is None:
            return f"From {self.fmt(self.low)} {self.unit}"
        return f"{self.fmt(self.low)} to {self.fmt(self.high)} {self.unit}"


def list_params(db: Database) -> list[Param]:
    rows = db.query("SELECT key, name, unit, low, high, decimals FROM params ORDER BY sort_order, name")
    return [Param(**dict(r)) for r in rows]


def now_local() -> datetime:
    return datetime.now().replace(second=0, microsecond=0)


def parse_number(raw: str) -> float | None:
    raw = raw.strip().replace(",", ".")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        raise ParamError(f"“{raw}” isn't a number.") from None


def parse_time(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return now_local().strftime(TIME_FORMAT)
    try:
        when = datetime.strptime(raw[:16], TIME_FORMAT)
    except ValueError:
        raise ParamError("Pick the date and time of the test.") from None
    if when > now_local() + timedelta(hours=1):
        raise ParamError("The test date is in the future.")
    return when.strftime(TIME_FORMAT)


def add_test(db: Database, taken_at: str, values: dict[str, float], note: str = "") -> int:
    params = {p.key: p for p in list_params(db)}
    clean: dict[str, float] = {}
    for key, value in values.items():
        if key not in params or value is None:
            continue
        lo, hi = PLAUSIBLE.get(key, (0, 1e9))
        if not lo <= value <= hi:
            p = params[key]
            raise ParamError(f"{p.name} of {value:g} {p.unit} looks like a typo (expected {lo:g} to {hi:g}).")
        clean[key] = value
    if not clean:
        raise ParamError("Enter at least one result.")
    with db.tx() as c:
        cur = c.execute("INSERT INTO tests(taken_at, note) VALUES(?, ?)", (taken_at, note.strip()[:MAX_NOTE]))
        test_id = cur.lastrowid
        c.executemany(
            "INSERT INTO readings(test_id, param_key, value) VALUES(?, ?, ?)",
            [(test_id, k, v) for k, v in clean.items()],
        )
    return test_id


def delete_test(db: Database, test_id: int) -> bool:
    if not db.one("SELECT 1 FROM tests WHERE id = ?", (test_id,)):
        return False
    db.execute("DELETE FROM tests WHERE id = ?", (test_id,))
    return True


def set_band(db: Database, key: str, low: float | None, high: float | None) -> None:
    if low is not None and high is not None and low >= high:
        raise ParamError("The lower limit has to be below the upper limit.")
    db.execute("UPDATE params SET low = ?, high = ? WHERE key = ?", (low, high, key))


def history(db: Database, key: str, since: str | None = None) -> list[tuple[datetime, float]]:
    sql = ("SELECT t.taken_at, r.value FROM readings r JOIN tests t ON t.id = r.test_id "
           "WHERE r.param_key = ?")
    args: tuple = (key,)
    if since:
        sql += " AND t.taken_at >= ?"
        args += (since,)
    rows = db.query(sql + " ORDER BY t.taken_at, t.id", args)
    return [(datetime.strptime(r["taken_at"], TIME_FORMAT), r["value"]) for r in rows]


def latest(db: Database) -> list[dict]:
    """For each parameter: its newest reading, the one before, and how it compares to the band."""
    out = []
    for p in list_params(db):
        rows = db.query(
            "SELECT t.taken_at, r.value FROM readings r JOIN tests t ON t.id = r.test_id "
            "WHERE r.param_key = ? ORDER BY t.taken_at DESC, t.id DESC LIMIT 2",
            (p.key,),
        )
        value = rows[0]["value"] if rows else None
        prev = rows[1]["value"] if len(rows) > 1 else None
        taken = datetime.strptime(rows[0]["taken_at"], TIME_FORMAT) if rows else None
        out.append({
            "param": p, "value": value, "text": p.fmt(value), "status": p.status(value),
            "change": None if prev is None or value is None else value - prev,
            "taken_at": taken, "age": age_text(taken),
        })
    return out


def recent_tests(db: Database, limit: int = 20) -> list[dict]:
    tests = db.query("SELECT id, taken_at, note FROM tests ORDER BY taken_at DESC, id DESC LIMIT ?", (limit,))
    if not tests:
        return []
    ids = [t["id"] for t in tests]
    marks = ",".join("?" * len(ids))
    values: dict[int, dict[str, float]] = {}
    for r in db.query(f"SELECT test_id, param_key, value FROM readings WHERE test_id IN ({marks})", tuple(ids)):
        values.setdefault(r["test_id"], {})[r["param_key"]] = r["value"]
    return [{
        "id": t["id"], "taken_at": datetime.strptime(t["taken_at"], TIME_FORMAT),
        "note": t["note"], "values": values.get(t["id"], {}),
    } for t in tests]


def age_text(when: datetime | None, now: datetime | None = None) -> str:
    if when is None:
        return "never tested"
    now = now or now_local()
    days = (now.date() - when.date()).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 14:
        return f"{days} days ago"
    if days < 60:
        return f"{days // 7} weeks ago"
    return when.strftime("%d %b %Y")
