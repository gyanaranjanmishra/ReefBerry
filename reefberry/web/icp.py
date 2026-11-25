"""ICP page: import a report, check it before saving, see results, history and dosing."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from ..services import icp as I
from ..services.charts import line_chart
from ..services.icp_elements import BY_SYM, ELEMENTS
from .common import checked_form, flash, get_db, render, require_setup_done

router = APIRouter()


def _litres(db) -> float:
    try:
        return float(db.get_setting("tank_volume_l") or 0)
    except (TypeError, ValueError):
        return 0.0


@router.get("/icp")
def icp_page(request: Request):
    require_setup_done(request)
    db = get_db(request)
    reports = I.reports(db)
    current = prev = None
    if reports:
        wanted = request.query_params.get("report")
        current = next((r for r in reports if str(r["id"]) == wanted), reports[0])
        idx = reports.index(current)
        prev = reports[idx + 1] if idx + 1 < len(reports) else None
    values = I.report_values(db, current["id"]) if current else {}
    prev_values = I.report_values(db, prev["id"]) if prev else {}
    tg = I.targets(db)
    litres = _litres(db)
    doses, salinity_low = I.dosing(values, tg, litres) if current else ([], False)
    groups = []
    for title, els in I.grouped():
        rows = []
        for el in els:
            v = values.get(el.sym)
            if v is None and el.sym not in prev_values:
                continue  # not measured in either report: leave it out
            pv = prev_values.get(el.sym)
            rows.append({
                "el": el, "value": I.show_value(v, el) or "not measured", "status": I.status(v, tg[el.sym]),
                "range": I.range_text(el, tg[el.sym]), "prev": I.show_value(pv, el),
                "trend": None if v is None or pv is None or v == pv else ("up" if v > pv else "down"),
            })
        if rows:
            groups.append((title, rows))
    counts = {"low": 0, "high": 0}
    for _, rows in groups:
        for r in rows:
            if r["status"] in counts:
                counts[r["status"]] += 1
    return render(
        request, "icp.html", reports=reports, current=current, prev=prev, groups=groups,
        doses=doses, salinity_low=salinity_low, litres=litres, counts=counts,
        dose_log=I.recent_doses(db), today=date.today().isoformat(),
    )


def _review(request: Request, *, values: dict, meta: dict, error: str | None = None, status: int = 200):
    tg = I.targets(get_db(request))
    groups = [(title, [{"el": el, "value": values.get(el.sym, ""), "range": I.range_text(el, tg[el.sym])}
                       for el in els]) for title, els in I.grouped()]
    return render(request, "icp_review.html", status_code=status, groups=groups, meta=meta, error=error,
                  today=date.today().isoformat())


@router.get("/icp/new")
def icp_new(request: Request):
    require_setup_done(request)
    return _review(request, values={}, meta={"sampled_on": date.today().isoformat(), "source": "hand"})


@router.post("/icp/upload")
async def icp_upload(request: Request):
    require_setup_done(request)
    form = await checked_form(request)
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        flash(request, "Choose a CSV or PDF file first.", "error")
        return RedirectResponse("/icp", status_code=303)
    data = await upload.read(I.MAX_UPLOAD + 1)
    try:
        parsed = I.parse_upload(upload.filename or "", data)
    except I.IcpError as exc:
        flash(request, str(exc), "error")
        return RedirectResponse("/icp", status_code=303)
    values = {sym: I.show_value(v, BY_SYM[sym]) for sym, v in parsed.values.items()}
    meta = {"sampled_on": parsed.sampled_on, "analysis_id": parsed.analysis_id, "lab": parsed.lab,
            "source": parsed.source, "filename": upload.filename or ""}
    return _review(request, values=values, meta=meta)


@router.post("/icp/save")
async def icp_save(request: Request):
    require_setup_done(request)
    form = await checked_form(request)
    db = get_db(request)
    entered = {el.sym: str(form.get(f"v_{el.sym}", "")).strip() for el in ELEMENTS}
    meta = {k: str(form.get(k, "")).strip() for k in ("sampled_on", "analysis_id", "lab", "note", "source", "filename")}
    try:
        values = {}
        for el in ELEMENTS:
            try:
                v = I.parse_value(entered[el.sym])
            except I.IcpError as exc:
                raise I.IcpError(f"{el.name}: {exc}") from None
            if v is not None:
                values[el.sym] = v
        rid = I.save_report(db, meta["sampled_on"], values, lab=meta["lab"],
                            analysis_id=meta["analysis_id"], note=meta["note"])
    except I.IcpError as exc:
        return _review(request, values=entered, meta=meta, error=str(exc), status=400)
    flash(request, f"ICP results from {meta['sampled_on']} saved ({len(values)} elements).")
    return RedirectResponse(f"/icp?report={rid}", status_code=303)


@router.post("/icp/{report_id}/delete")
async def icp_delete(request: Request, report_id: int):
    require_setup_done(request)
    await checked_form(request)
    if I.delete_report(get_db(request), report_id):
        flash(request, "ICP report deleted.")
    return RedirectResponse("/icp", status_code=303)


@router.get("/icp/element/{sym}")
def icp_element(request: Request, sym: str):
    require_setup_done(request)
    el = BY_SYM.get(sym)
    if not el:
        raise HTTPException(status_code=404, detail="There's no such element.")
    db = get_db(request)
    tg = I.targets(db)[sym]
    hist = I.history(db, sym)
    chart = line_chart([(t, v) for t, v, _ in hist], low=tg[0], high=tg[2], unit=el.unit,
                       decimals=el.decimals, label=el.name)
    rows = [{"date": t, "value": I.show_value(v, el), "status": I.status(v, tg), "report": rid}
            for t, v, rid in reversed(hist)]
    return render(request, "icp_element.html", el=el, chart=chart, rows=rows,
                  range=I.range_text(el, tg), target=tg)


@router.get("/icp/targets")
def icp_targets(request: Request):
    require_setup_done(request)
    tg = I.targets(get_db(request))
    groups = [(title, [{"el": el, "t": ["" if x is None else f"{x:g}" for x in tg[el.sym]],
                        "changed": tg[el.sym] != (el.min, el.opt, el.max)} for el in els])
              for title, els in I.grouped()]
    return render(request, "icp_targets.html", groups=groups)


@router.post("/icp/targets")
async def icp_targets_save(request: Request):
    require_setup_done(request)
    form = await checked_form(request)
    db = get_db(request)
    if form.get("reset"):
        I.reset_targets(db)
        flash(request, "Targets reset to the Fauna Marin defaults.")
        return RedirectResponse("/icp/targets", status_code=303)
    new = {}
    try:
        for el in ELEMENTS:
            new[el.sym] = tuple(I.parse_value(str(form.get(f"{k}_{el.sym}", ""))) for k in ("min", "opt", "max"))
        I.set_targets(db, new)
    except I.IcpError as exc:
        flash(request, f"{exc} Nothing was saved.", "error")
        return RedirectResponse("/icp/targets", status_code=303)
    flash(request, "Targets saved.")
    return RedirectResponse("/icp/targets", status_code=303)


@router.post("/icp/doses")
async def icp_log_dose(request: Request):
    require_setup_done(request)
    form = await checked_form(request)
    try:
        ml = float(str(form.get("ml", "")).replace(",", "."))
    except ValueError:
        ml = 0
    rid = str(form.get("report_id", ""))
    try:
        I.log_dose(get_db(request), str(form.get("dosed_on", "")), str(form.get("product", "")), ml,
                   int(rid) if rid.isdigit() else None)
        flash(request, f"Logged {ml:g} ml {form.get('product')}.")
    except I.IcpError as exc:
        flash(request, str(exc), "error")
    return RedirectResponse("/icp#dose-log", status_code=303)


@router.post("/icp/doses/{dose_id}/delete")
async def icp_delete_dose(request: Request, dose_id: int):
    require_setup_done(request)
    await checked_form(request)
    I.delete_dose(get_db(request), dose_id)
    flash(request, "Dose removed from the log.")
    return RedirectResponse("/icp#dose-log", status_code=303)
