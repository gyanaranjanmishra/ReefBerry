"""Dashboard and Settings pages, plus the JSON endpoint the dashboard polls."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from ..security import password_problem, verify_password
from ..services.controller import DEFAULTS as AUTOMATION_DEFAULTS
from ..services.devices import list_devices, visible_outlets
from ..services.notify import SMTP_PRESETS
from ..users import set_password
from .common import (
    checked_form, flash, get_db, render, require_setup_done, sign_in,
)
from .forms import alert_settings_view, parse_tank_form

router = APIRouter()


@router.get("/")
def dashboard(request: Request):
    require_setup_done(request)
    db = get_db(request)
    return render(
        request, "dashboard.html",
        outlets=visible_outlets(db), volume=db.get_setting("tank_volume_l"),
        automation={k: db.get_setting(k, v) for k, v in AUTOMATION_DEFAULTS.items()},
    )


@router.get("/settings")
def settings_page(request: Request):
    require_setup_done(request)
    db = get_db(request)
    return render(
        request, "settings.html",
        values={"tank_name": db.get_setting("tank_name", ""), "tank_volume_l": db.get_setting("tank_volume_l", "")},
        tank_error=None,
        devices=list_devices(db), kasa_username=db.get_setting("kasa_username", ""),
        has_kasa_password=bool(db.get_setting("kasa_password")),
        alerts=alert_settings_view(db), presets=SMTP_PRESETS, back="/settings",
        automation={k: db.get_setting(k, v) for k, v in AUTOMATION_DEFAULTS.items()},
    )


@router.post("/settings/tank")
async def settings_tank(request: Request):
    require_setup_done(request)
    form = await checked_form(request)
    values, error = parse_tank_form(form)
    if error:
        flash(request, error, "error")
    else:
        get_db(request).set_settings(values)
        flash(request, "Tank details saved.")
    return RedirectResponse("/settings#tank", status_code=303)


@router.post("/settings/password")
async def settings_password(request: Request):
    user = require_setup_done(request)
    form = await checked_form(request)
    db = get_db(request)
    row = db.one("SELECT password_hash FROM users WHERE id = ?", (user["id"],))
    if not verify_password(str(form.get("current", "")), row["password_hash"]):
        flash(request, "Your current password isn't right.", "error")
    elif problem := password_problem(str(form.get("password", "")), str(form.get("confirm", ""))):
        flash(request, problem, "error")
    else:
        set_password(db, user["username"], str(form["password"]))
        sign_in(request, user["id"])  # keep this browser signed in; others are signed out
        flash(request, "Password changed. Other devices signed in to this account have been signed out.")
    return RedirectResponse("/settings#account", status_code=303)
