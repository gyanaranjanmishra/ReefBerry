"""Water tests page: enter results, see each parameter's chart and recent tests."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from ..services import params as P
from ..services.charts import line_chart
from .common import checked_form, flash, get_db, render, require_setup_done

router = APIRouter()

RANGES = {"30": ("30 days", 30), "90": ("90 days", 90), "365": ("1 year", 365), "all": ("All", None)}
DEFAULT_RANGE = "90"


def _page(request: Request, *, form_values: dict | None = None, error: str | None = None, status: int = 200):
    db = get_db(request)
    key = request.query_params.get("range", DEFAULT_RANGE)
    if key not in RANGES:
        key = DEFAULT_RANGE
    days = RANGES[key][1]
    now = P.now_local()
    start = now - timedelta(days=days) if days else None
    since = start.strftime(P.TIME_FORMAT) if start else None
    cards = []
    for item in P.latest(db):
        p = item["param"]
        points = P.history(db, p.key, since)
        cards.append({
            **item,
            "count": len(points),
            "chart": line_chart(
                points, low=p.low, high=p.high, unit=p.unit, decimals=p.decimals, label=p.name,
                start=start, end=now if start else None,
            ),
        })
    return render(
        request, "parameters.html", status_code=status,
        cards=cards, params=P.list_params(db), tests=P.recent_tests(db),
        ranges=RANGES, range_key=key,
        form=form_values or {"taken_at": now.strftime(P.TIME_FORMAT)},
        max_time=(now + timedelta(hours=1)).strftime(P.TIME_FORMAT), error=error,
    )


@router.get("/parameters")
def parameters(request: Request):
    require_setup_done(request)
    return _page(request)


@router.post("/parameters")
async def add_test(request: Request):
    require_setup_done(request)
    form = await checked_form(request)
    db = get_db(request)
    params = P.list_params(db)
    entered = {k: str(form.get(k, "")) for k in ["taken_at", "note"] + [p.key for p in params]}
    try:
        taken_at = P.parse_time(entered["taken_at"])
        values = {p.key: P.parse_number(entered[p.key]) for p in params}
        P.add_test(db, taken_at, values, entered["note"])
    except P.ParamError as exc:
        return _page(request, form_values=entered, error=str(exc), status=400)
    saved = [p.name for p in params if values.get(p.key) is not None]
    flash(request, f"Saved {', '.join(saved)}.")
    return RedirectResponse("/parameters", status_code=303)


@router.post("/parameters/{test_id}/delete")
async def delete_test(request: Request, test_id: int):
    require_setup_done(request)
    await checked_form(request)
    if P.delete_test(get_db(request), test_id):
        flash(request, "Test deleted.")
    else:
        flash(request, "That test was already gone.", "warn")
    return RedirectResponse("/parameters#tests", status_code=303)


@router.post("/settings/parameters")
async def settings_parameters(request: Request):
    require_setup_done(request)
    form = await checked_form(request)
    db = get_db(request)
    bands = {}
    try:
        for p in P.list_params(db):
            low = P.parse_number(str(form.get(f"{p.key}_low", "")))
            high = P.parse_number(str(form.get(f"{p.key}_high", "")))
            if low is not None and high is not None and low >= high:
                raise P.ParamError(f"{p.name}: the lower limit has to be below the upper limit.")
            bands[p.key] = (low, high)
    except P.ParamError as exc:
        flash(request, f"{exc} Nothing was saved.", "error")
        return RedirectResponse("/settings#parameters", status_code=303)
    for key, (low, high) in bands.items():
        P.set_band(db, key, low, high)
    flash(request, "Green bands saved.")
    return RedirectResponse("/settings#parameters", status_code=303)
