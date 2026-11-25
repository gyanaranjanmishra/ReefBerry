"""ICP reports: reading Fauna Marin CSV and PDF files, storing results, targets and dosing."""

from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from ..db import Database, utcnow
from .icp_elements import BY_SYM, ELEMENTS, GROUPS, Element

MAX_UPLOAD = 5 * 1024 * 1024
ND = 0.0  # stored value for "not detected"


class IcpError(ValueError):
    """A problem with a file or a form, worded for the person importing it."""


@dataclass
class Parsed:
    values: dict[str, float] = field(default_factory=dict)   # sym -> value (0 = not detected)
    sampled_on: str = ""
    analysis_id: str = ""
    lab: str = ""
    source: str = ""


# ---------- reading values ----------
def parse_value(raw: str) -> float | None:
    """'' -> not measured (None), 'n.d.' -> not detected (0), '4,04' -> 4.04."""
    text = str(raw).strip().lower().replace("µg/l", "").replace("mg/l", "").strip()
    if text in ("", "-", "not measured", "nm"):
        return None
    if text in ("n.d.", "n.d", "nd", "<dl", "not detected"):
        return ND
    text = text.lstrip("<").replace(" ", "").replace(",", ".")
    try:
        value = float(text)
    except ValueError:
        raise IcpError(f"“{raw}” isn't a number. Use n.d. for not detected, or leave it empty.") from None
    if value < 0 or math.isinf(value) or math.isnan(value):
        raise IcpError(f"“{raw}” can't be a measured value.")
    return value


def show_value(value: float | None, el: Element) -> str:
    if value is None:
        return ""
    if value == ND:
        return "n.d."
    return f"{value:.{el.decimals}f}" if value < 1000 else f"{value:.0f}"


def _date(text: str, formats: tuple[str, ...]) -> str:
    for fmt in formats:
        try:
            return datetime.strptime(text.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def parse_csv(data: bytes) -> Parsed:
    text = data.decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    if len(rows) < 2 or "id" not in rows[0]:
        raise IcpError("This doesn't look like a Fauna Marin ICP CSV export.")
    head = [h.strip() for h in rows[0]]
    row = dict(zip(head, rows[1]))
    out = Parsed(lab="Fauna Marin", source="csv", analysis_id=row.get("id", "").strip())
    for el in ELEMENTS:
        if el.csv in row:
            value = parse_value(row[el.csv])
            if value is not None:
                out.values[el.sym] = value
    out.sampled_on = _date(row.get("analysis_date", "")[:10], ("%Y-%m-%d",))
    if not out.values:
        raise IcpError("No element values were found in this CSV.")
    return out


_LINE = re.compile(r"^(?:[A-Z][a-z]+(?: \([^)]*\))? )?([A-Z][a-z]?) (n\.d\.|not measured|[\d.,]+)(?=\s|$)")


def parse_pdf(data: bytes) -> Parsed:
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except ImportError:
        raise IcpError("Reading PDFs needs the pypdf package. Run the installer again to add it.") from None
    except Exception:
        raise IcpError("This PDF couldn't be read.") from None
    return parse_pdf_text(text)


def parse_pdf_text(text: str) -> Parsed:
    if "ICP" not in text and "Reference Range" not in text:
        raise IcpError("This doesn't look like an ICP report.")
    out = Parsed(lab="Fauna Marin" if "Fauna Marin" in text or "Sample ID" in text else "", source="pdf")
    for line in text.splitlines():
        m = _LINE.match(line.strip())
        if not m or m.group(1) not in BY_SYM or m.group(1) in out.values:
            continue
        value = parse_value(m.group(2))
        if value is not None:
            out.values[m.group(1)] = value
    if m := re.search(r"Analysis ID:\s*(\S+)", text):
        out.analysis_id = m.group(1)
    if m := re.search(r"Sampling Date:\s*([\d./-]+)", text):
        out.sampled_on = _date(m.group(1), ("%m-%d-%Y", "%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"))
    if not out.values:
        raise IcpError("No element values were found in this PDF.")
    return out


def parse_upload(filename: str, data: bytes) -> Parsed:
    if not data:
        raise IcpError("Choose a CSV or PDF file first.")
    if len(data) > MAX_UPLOAD:
        raise IcpError("That file is too large (over 5 MB).")
    if data[:5] == b"%PDF-" or filename.lower().endswith(".pdf"):
        return parse_pdf(data)
    return parse_csv(data)


# ---------- storing reports ----------
def save_report(db: Database, sampled_on: str, values: dict[str, float], *,
                lab: str = "", analysis_id: str = "", note: str = "") -> int:
    if not _date(sampled_on, ("%Y-%m-%d",)):
        raise IcpError("Pick the date the sample was taken.")
    if sampled_on > date.today().isoformat():
        raise IcpError("The sample date is in the future.")
    values = {k: v for k, v in values.items() if k in BY_SYM and v is not None}
    if not values:
        raise IcpError("Enter at least one result.")
    analysis_id = analysis_id.strip()[:40]
    if analysis_id and db.one("SELECT 1 FROM icp_reports WHERE lab = ? AND analysis_id = ?", (lab, analysis_id)):
        raise IcpError(f"Analysis {analysis_id} is already saved.")
    with db.tx() as c:
        cur = c.execute(
            "INSERT INTO icp_reports(lab, analysis_id, sampled_on, note, created_at) VALUES(?, ?, ?, ?, ?)",
            (lab[:40], analysis_id, sampled_on, note.strip()[:200], utcnow()),
        )
        rid = cur.lastrowid
        c.executemany("INSERT INTO icp_values(report_id, element, value) VALUES(?, ?, ?)",
                      [(rid, k, v) for k, v in values.items()])
    return rid


def delete_report(db: Database, report_id: int) -> bool:
    if not db.one("SELECT 1 FROM icp_reports WHERE id = ?", (report_id,)):
        return False
    db.execute("DELETE FROM icp_reports WHERE id = ?", (report_id,))
    return True


def reports(db: Database) -> list[dict]:
    rows = db.query(
        "SELECT r.id, r.lab, r.analysis_id, r.sampled_on, r.note, COUNT(v.element) AS n "
        "FROM icp_reports r LEFT JOIN icp_values v ON v.report_id = r.id "
        "GROUP BY r.id ORDER BY r.sampled_on DESC, r.id DESC"
    )
    return [dict(r) for r in rows]


def report_values(db: Database, report_id: int) -> dict[str, float]:
    return {r["element"]: r["value"] for r in
            db.query("SELECT element, value FROM icp_values WHERE report_id = ?", (report_id,))}


def history(db: Database, sym: str) -> list[tuple[datetime, float, int]]:
    rows = db.query(
        "SELECT r.sampled_on, v.value, r.id FROM icp_values v JOIN icp_reports r ON r.id = v.report_id "
        "WHERE v.element = ? ORDER BY r.sampled_on, r.id", (sym,))
    return [(datetime.strptime(r["sampled_on"], "%Y-%m-%d"), r["value"], r["id"]) for r in rows]


# ---------- targets ----------
def targets(db: Database) -> dict[str, tuple[float | None, float | None, float | None]]:
    out = {e.sym: (e.min, e.opt, e.max) for e in ELEMENTS}
    for r in db.query("SELECT element, min, opt, max FROM icp_targets"):
        if r["element"] in out:
            out[r["element"]] = (r["min"], r["opt"], r["max"])
    return out


def set_targets(db: Database, new: dict[str, tuple[float | None, float | None, float | None]]) -> None:
    for sym, (lo, opt, hi) in new.items():
        name = BY_SYM[sym].name
        if lo is not None and hi is not None and lo > hi:
            raise IcpError(f"{name}: the minimum is above the maximum.")
        if opt is not None and ((lo is not None and opt < lo) or (hi is not None and opt > hi)):
            raise IcpError(f"{name}: the target has to be between the minimum and maximum.")
    with db.tx() as c:
        for sym, (lo, opt, hi) in new.items():
            e = BY_SYM[sym]
            if (lo, opt, hi) == (e.min, e.opt, e.max):
                c.execute("DELETE FROM icp_targets WHERE element = ?", (sym,))
            else:
                c.execute(
                    "INSERT INTO icp_targets(element, min, opt, max) VALUES(?, ?, ?, ?) "
                    "ON CONFLICT(element) DO UPDATE SET min = excluded.min, opt = excluded.opt, max = excluded.max",
                    (sym, lo, opt, hi))


def reset_targets(db: Database) -> None:
    db.execute("DELETE FROM icp_targets")


def status(value: float | None, target: tuple) -> str | None:
    if value is None:
        return None
    lo, _, hi = target
    if lo is not None and value < lo:
        return "low"
    if hi is not None and value > hi:
        return "high"
    return "ok"


def range_text(el: Element, target: tuple) -> str:
    lo, opt, hi = target
    fmt = lambda v: "n.d." if v == 0 else ("" if v is None else f"{v:g}")  # noqa: E731
    if lo is None and hi is None:
        return ""
    if lo is None:
        return "n.d." if hi == 0 else f"max. {fmt(hi)}"
    if opt is None:
        return f"{fmt(lo)} – {fmt(hi)}" if hi is not None else f"from {fmt(lo)}"
    return f"{fmt(lo)} – {fmt(opt)} – {fmt(hi)}" if hi is not None else f"{fmt(lo)} – {fmt(opt)}"


# ---------- dosing ----------
@dataclass
class DoseRow:
    el: Element
    measured: float
    target: float
    total_ml: float
    days: int
    per_day_ml: float


def dosing(values: dict[str, float], tg: dict, litres: float) -> tuple[list[DoseRow], bool]:
    """Fauna Marin single-dose calculation for every element below its minimum.

    Returns the doses (most urgent first) and whether to raise salinity before dosing:
    sodium below its target and sulfur below its minimum usually means the whole
    salinity is low, which no single element dose fixes.
    """
    rows: list[DoseRow] = []
    if litres and litres > 0:
        for el in ELEMENTS:
            measured = values.get(el.sym)
            lo, opt, _ = tg[el.sym]
            if el.dose is None or measured is None or lo is None or opt is None or measured >= lo:
                continue
            d = el.dose
            total = d.a / 100 / d.b * litres * (opt - measured)
            if total <= 0:
                continue
            pct = total / litres * 100
            days = 1 if pct <= d.cap else math.ceil(pct / d.cap)
            rows.append(DoseRow(el, measured, opt, total, days, total / days))
    rows.sort(key=lambda r: (r.el.dose.priority, ELEMENTS.index(r.el)))
    na, s = values.get("Na"), values.get("S")
    na_opt, s_min = tg["Na"][1], tg["S"][0]
    salinity_low = (na is not None and s is not None and na_opt is not None and s_min is not None
                    and na < na_opt and s < s_min)
    return rows, salinity_low


def log_dose(db: Database, dosed_on: str, product: str, ml: float, report_id: int | None = None) -> None:
    if not _date(dosed_on, ("%Y-%m-%d",)):
        raise IcpError("Pick the day you dosed.")
    if not product.strip() or not (0 < ml <= 10_000):
        raise IcpError("Enter the product and an amount in ml.")
    db.execute("INSERT INTO dose_log(dosed_on, product, ml, report_id) VALUES(?, ?, ?, ?)",
               (dosed_on, product.strip()[:60], round(ml, 2), report_id))


def recent_doses(db: Database, limit: int = 30) -> list[dict]:
    return [dict(r) for r in db.query(
        "SELECT id, dosed_on, product, ml FROM dose_log ORDER BY dosed_on DESC, id DESC LIMIT ?", (limit,))]


def delete_dose(db: Database, dose_id: int) -> None:
    db.execute("DELETE FROM dose_log WHERE id = ?", (dose_id,))


def grouped(elements=ELEMENTS) -> list[tuple[str, list[Element]]]:
    return [(title, [e for e in elements if e.group == key]) for key, title in GROUPS.items()]
