"""First-run setup wizard: tank -> power devices -> alerts (the account is created on the Pi)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from ..services.devices import list_devices
from ..services.notify import SMTP_PRESETS
from .common import checked_form, get_db, has_users, render, require_user
from .forms import alert_settings_view, parse_tank_form

router = APIRouter(prefix="/setup")

STEPS = [
    ("tank", "Tank"),
    ("devices", "Power devices"),
    ("alerts", "Alerts"),
]


def _go(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


@router.get("")
def setup_start(request: Request):
    db = get_db(request)
    require_user(request)
    if db.get_setting("setup_complete", False):
        return _go("/")
    if not db.get_setting("tank_volume_l"):
        return _go("/setup/tank")
    return _go("/setup/devices")


# ---------- no account yet ----------
# Accounts are only created on the Pi itself (`sudo reefberry user add`, which the
# installer runs), so nobody on the network can claim a fresh install.
@router.get("/no-account")
def no_account_page(request: Request):
    if has_users(get_db(request)):
        return _go("/login")
    return render(request, "setup_no_account.html")


# ---------- 2. tank ----------
@router.get("/tank")
def tank_page(request: Request):
    require_user(request)
    db = get_db(request)
    return render(
        request, "setup_tank.html", steps=STEPS, step="tank", error=None,
        values={"tank_name": db.get_setting("tank_name", ""), "tank_volume_l": db.get_setting("tank_volume_l", "")},
    )


@router.post("/tank")
async def tank_save(request: Request):
    require_user(request)
    form = await checked_form(request)
    values, error = parse_tank_form(form)
    if error:
        return render(request, "setup_tank.html", status_code=400, steps=STEPS, step="tank", error=error, values=values)
    get_db(request).set_settings(values)
    return _go("/setup/devices")


# ---------- 3. devices ----------
@router.get("/devices")
def devices_page(request: Request):
    require_user(request)
    db = get_db(request)
    return render(
        request, "setup_devices.html", steps=STEPS, step="devices",
        devices=list_devices(db), kasa_username=db.get_setting("kasa_username", ""),
        has_kasa_password=bool(db.get_setting("kasa_password")), back="/setup/devices",
    )


# ---------- 4. alerts ----------
@router.get("/alerts")
def alerts_page(request: Request):
    require_user(request)
    return render(
        request, "setup_alerts.html", steps=STEPS, step="alerts",
        alerts=alert_settings_view(get_db(request)), presets=SMTP_PRESETS, back="/setup/alerts",
    )


@router.post("/finish")
async def finish(request: Request):
    require_user(request)
    await checked_form(request)
    db = get_db(request)
    if not db.get_setting("tank_volume_l"):
        return _go("/setup/tank")
    db.set_setting("setup_complete", True)
    db.log_event("setup_complete")
    return _go("/")
