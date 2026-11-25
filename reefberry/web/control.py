"""Dashboard API: outlet states, switching, feed and water change modes; automation settings."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from ..security import csrf_ok
from ..services.controller import DEFAULTS, HOLD_REASONS, LIMITS, ControlError, Controller
from ..services.devices import visible_outlets
from .. import __version__
from ..services.kasa_service import ROLES
from .common import (
    Redirect, checked_form, current_user, flash, get_db, is_local, render,
    require_operator, require_setup_done, require_viewer, screen_allowed, screen_can_control,
)

router = APIRouter()

EVENT_KINDS = ("on", "off", "mode", "alert", "switch_failed", "released")


def get_controller(request: Request) -> Controller:
    return request.app.state.controller


def _check_api_csrf(request: Request) -> None:
    if not csrf_ok(request, request.headers.get("x-csrf-token")):
        raise HTTPException(status_code=403, detail="This page is out of date. Reload it and try again.")


def _recent_events(request: Request, names: dict[str, str], limit: int = 8) -> list[dict]:
    rows = get_db(request).query(
        f"SELECT ts, kind, outlet_id, detail FROM events WHERE kind IN ({','.join('?' * len(EVENT_KINDS))}) "
        "ORDER BY id DESC LIMIT ?",
        (*EVENT_KINDS, limit),
    )
    out = []
    for r in rows:
        name = names.get(r["outlet_id"], "") if r["outlet_id"] else ""
        if r["kind"] in ("on", "off"):
            text = f"{name} turned {r['kind']}: {r['detail']}"
        elif r["kind"] == "switch_failed":
            text = f"Couldn't switch {name}: {r['detail']}"
        elif r["kind"] == "released":
            text = f"{name}: {r['detail'].lower()}"
        else:
            text = r["detail"]
        out.append({"ts": r["ts"], "kind": r["kind"], "text": text})
    return out


@router.get("/api/outlets")
async def api_outlets(request: Request):
    require_viewer(request)
    ctrl = get_controller(request)
    await ctrl.ensure_fresh()
    view = ctrl.view()
    db = get_db(request)
    names = {r["id"]: r["display_name"] for r in db.query("SELECT id, display_name FROM outlets")}
    outlets = []
    for o in visible_outlets(db):
        live = ctrl.live.get(o["id"], {})
        st = view["statuses"].get(o["id"], {})
        outlets.append({
            "id": o["id"],
            "name": o["display_name"],
            "role": o["role"],
            "role_label": ROLES.get(o["role"], "Other"),
            "online": live.get("online", False),
            "is_on": live.get("is_on"),
            "error": live.get("error"),
            "held_by": st.get("held_by"),
            "hold_reason": HOLD_REASONS.get(st.get("held_by") or ""),
            "next_on": view["next_on"].get(o["id"]),
        })
    return {
        "now": view["now"],
        "ready": view["ready"],
        "simulated": view["simulated"],
        "version": __version__,
        "mode": view["mode"],
        "outlets": outlets,
        "events": _recent_events(request, names),
    }


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected a JSON object.")
    return body


@router.post("/api/outlets/{outlet_id}/switch")
async def api_switch(outlet_id: str, request: Request):
    require_operator(request)
    _check_api_csrf(request)
    body = await _json_body(request)
    if not isinstance(body.get("on"), bool):
        raise HTTPException(status_code=400, detail='Send {"on": true} or {"on": false}.')
    try:
        await get_controller(request).user_switch(outlet_id, body["on"])
    except ControlError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"ok": True}


MODE_ACTIONS = {
    "feed-start": "start_feed",
    "feed-end": "end_feed",
    "water-change-start": "start_water_change",
    "water-change-done": "finish_water_change",
}


@router.post("/api/mode/{action}")
async def api_mode(action: str, request: Request):
    require_operator(request)
    _check_api_csrf(request)
    method = MODE_ACTIONS.get(action)
    if method is None:
        raise HTTPException(status_code=404, detail="Unknown mode action.")
    try:
        await getattr(get_controller(request), method)()
    except ControlError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"ok": True}


@router.post("/settings/automation")
async def settings_automation(request: Request):
    require_setup_done(request)
    form = await checked_form(request)
    values, problems = {}, []
    for key, default in DEFAULTS.items():
        raw = str(form.get(key, default)).strip().replace(",", ".")
        lo, hi = LIMITS[key]
        try:
            num = float(raw)
        except ValueError:
            problems.append(raw)
            continue
        if not lo <= num <= hi:
            problems.append(raw)
            continue
        values[key] = int(num) if num == int(num) else num
    if problems:
        flash(request, "Some times weren't valid numbers in the allowed range, so nothing was saved.", "error")
    else:
        get_db(request).set_settings(values)
        flash(request, "Automation times saved.")
    return RedirectResponse("/settings#automation", status_code=303)


# ---------- the Pi's own screen ----------
SCREEN_MODES = {"off": "Off", "view": "View only", "control": "View and control"}


@router.get("/screen")
def screen(request: Request):
    """Read-only, full-screen dashboard for a monitor plugged into the Pi."""
    if not current_user(request) and not screen_allowed(request):
        if is_local(request):
            raise HTTPException(
                status_code=403,
                detail="The Pi screen view is turned off. Turn it on with:  sudo reefberry screen on",
            )
        raise Redirect("/login?next=/screen")
    db = get_db(request)
    if not db.get_setting("setup_complete", False):
        raise HTTPException(status_code=503, detail="Finish the setup on another device first.")
    control = bool(current_user(request)) or screen_can_control(request)
    return render(
        request, "screen.html", outlets=visible_outlets(db), volume=db.get_setting("tank_volume_l"),
        control=control, automation={k: db.get_setting(k, v) for k, v in DEFAULTS.items()},
    )


@router.post("/settings/screen")
async def settings_screen(request: Request):
    require_setup_done(request)
    form = await checked_form(request)
    mode = str(form.get("screen_mode", "off"))
    if mode not in SCREEN_MODES:
        mode = "off"
    get_db(request).set_settings({"screen_enabled": mode != "off", "screen_control": mode == "control"})
    flash(request, f"Pi screen: {SCREEN_MODES[mode].lower()}.")
    return RedirectResponse("/settings#screen", status_code=303)
