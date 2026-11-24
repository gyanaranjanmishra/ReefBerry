"""Form parsing and the device/alert actions shared by the setup wizard and Settings."""

from __future__ import annotations

import ipaddress
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from starlette.datastructures import FormData

from ..db import Database
from ..services.devices import list_devices, save_found_devices
from ..services.kasa_service import ROLES
from ..services.notify import NotifyError, send_email, send_ntfy
from .common import checked_form, flash, get_db, get_kasa, require_user

router = APIRouter()

BACK_TARGETS = {"/setup/devices", "/setup/alerts", "/settings"}
MAX_VOLUME_L = 100_000


def _back(form: FormData, default: str) -> str:
    target = str(form.get("back", default))
    return target if target in BACK_TARGETS else default


def parse_tank_form(form: FormData) -> tuple[dict, str | None]:
    name = str(form.get("tank_name", "")).strip()[:60]
    raw_volume = str(form.get("tank_volume_l", "")).strip().replace(",", ".")
    values = {"tank_name": name, "tank_volume_l": raw_volume}
    if not name:
        return values, "Give your tank a name."
    try:
        volume = float(raw_volume)
    except ValueError:
        return values, "Enter the total system volume as a number of litres, for example 250."
    if not (1 <= volume <= MAX_VOLUME_L):
        return values, "The volume should be between 1 and 100,000 litres."
    values["tank_volume_l"] = round(volume, 1)
    return values, None


# ---------- Kasa login, scan and outlet setup ----------
@router.post("/devices/credentials")
async def save_kasa_credentials(request: Request):
    require_user(request)
    form = await checked_form(request)
    db = get_db(request)
    username = str(form.get("kasa_username", "")).strip()
    password = str(form.get("kasa_password", ""))
    if form.get("clear"):
        db.set_settings({"kasa_username": "", "kasa_password": ""})
        flash(request, "TP-Link login removed.")
    else:
        db.set_setting("kasa_username", username)
        if password:
            db.set_setting("kasa_password", password)
        flash(request, "TP-Link login saved. Scan again to read devices that needed it.")
    return RedirectResponse(_back(form, "/setup/devices"), status_code=303)


def _parse_hosts(raw: str) -> tuple[list[str], list[str]]:
    good, bad = [], []
    for part in raw.replace(";", ",").replace("\n", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ipaddress.ip_address(part)
            good.append(part)
        except ValueError:
            bad.append(part)
    return good, bad


@router.post("/devices/scan")
async def scan_devices(request: Request):
    require_user(request)
    form = await checked_form(request)
    db = get_db(request)
    hosts, bad = _parse_hosts(str(form.get("hosts", "")))
    if bad:
        flash(request, f"Skipped addresses that aren't valid IPs: {', '.join(bad)}", "warn")
    found = await get_kasa(request).discover(extra_hosts=hosts)
    save_found_devices(db, found)
    outlets = sum(len(d.outlets) for d in found)
    locked = [d.host for d in found if d.needs_auth]
    if not found:
        flash(
            request,
            "No devices answered. Check that the plugs are on the same Wi-Fi as the Pi, "
            "or type their IP addresses below and scan again.",
            "warn",
        )
    else:
        flash(request, f"Found {len(found)} device(s) with {outlets} outlet(s).")
    if locked:
        flash(
            request,
            f"{', '.join(locked)} need your TP-Link account login before their outlets can be read. "
            "Add it above, then scan again.",
            "warn",
        )
    db.log_event("device_scan", detail=f"{len(found)} devices")
    return RedirectResponse(_back(form, "/setup/devices"), status_code=303)


@router.post("/devices/save")
async def save_outlets(request: Request):
    require_user(request)
    form = await checked_form(request)
    db = get_db(request)
    icons = request.app.state.icons
    with db.tx() as c:
        for dev in list_devices(db):
            c.execute(
                "UPDATE devices SET hidden = ? WHERE id = ?",
                (1 if form.get(f"devhide__{dev['id']}") else 0, dev["id"]),
            )
            for o in dev["outlets"]:
                oid = o["id"]
                name = str(form.get(f"name__{oid}", o["display_name"])).strip()[:40] or o["kasa_alias"] or "Outlet"
                role = str(form.get(f"role__{oid}", o["role"]))
                if role not in ROLES:
                    role = "other"
                icon = str(form.get(f"icon__{oid}", o.get("icon") or "")).strip()
                if icon and icons.path_for(icon) is None:
                    icon = ""
                c.execute(
                    "UPDATE outlets SET display_name = ?, role = ?, icon = ?, hidden = ?, alert_enabled = ? WHERE id = ?",
                    (
                        name, role, icon or None,
                        1 if form.get(f"hide__{oid}") else 0,
                        1 if form.get(f"alert__{oid}") else 0,
                        oid,
                    ),
                )
    flash(request, "Outlets saved.")
    target = _back(form, "/setup/devices")
    if target == "/setup/devices" and form.get("continue"):
        target = "/setup/alerts"
    return RedirectResponse(target, status_code=303)


# ---------- alerts ----------
def alert_settings_view(db: Database) -> dict:
    topic = db.get_setting("ntfy_topic") or f"reefberry-{secrets.token_hex(5)}"
    return {
        "email_enabled": bool(db.get_setting("email_enabled", False)),
        "smtp_host": db.get_setting("smtp_host", "smtp.gmail.com"),
        "smtp_port": db.get_setting("smtp_port", 587),
        "smtp_security": db.get_setting("smtp_security", "starttls"),
        "smtp_username": db.get_setting("smtp_username", ""),
        "has_smtp_password": bool(db.get_setting("smtp_password")),
        "email_from": db.get_setting("email_from", ""),
        "email_to": db.get_setting("email_to", ""),
        "ntfy_enabled": bool(db.get_setting("ntfy_enabled", False)),
        "ntfy_server": db.get_setting("ntfy_server", "https://ntfy.sh"),
        "ntfy_topic": topic,
        "has_ntfy_token": bool(db.get_setting("ntfy_token")),
    }


@router.post("/alerts/save")
async def save_alerts(request: Request):
    require_user(request)
    form = await checked_form(request)
    db = get_db(request)
    target = _back(form, "/setup/alerts")

    try:
        port = int(str(form.get("smtp_port", "587")).strip() or 587)
    except ValueError:
        flash(request, "The mail server port must be a number, usually 587.", "error")
        return RedirectResponse(target, status_code=303)
    security = str(form.get("smtp_security", "starttls"))
    if security not in {"starttls", "ssl", "none"}:
        security = "starttls"
    topic = str(form.get("ntfy_topic", "")).strip()
    if topic and not all(ch.isalnum() or ch in "-_" for ch in topic):
        flash(request, "The ntfy topic can only use letters, numbers, dashes and underscores.", "error")
        return RedirectResponse(target, status_code=303)

    values = {
        "email_enabled": bool(form.get("email_enabled")),
        "smtp_host": str(form.get("smtp_host", "")).strip(),
        "smtp_port": port,
        "smtp_security": security,
        "smtp_username": str(form.get("smtp_username", "")).strip(),
        "email_from": str(form.get("email_from", "")).strip(),
        "email_to": str(form.get("email_to", "")).strip(),
        "ntfy_enabled": bool(form.get("ntfy_enabled")),
        "ntfy_server": str(form.get("ntfy_server", "https://ntfy.sh")).strip() or "https://ntfy.sh",
        "ntfy_topic": topic,
    }
    # Secrets: a blank field keeps the saved value.
    if form.get("smtp_password"):
        values["smtp_password"] = str(form.get("smtp_password")).replace(" ", "")
    if form.get("ntfy_token"):
        values["ntfy_token"] = str(form.get("ntfy_token")).strip()
    db.set_settings(values)

    action = form.get("action", "save")
    if action == "test_email":
        try:
            send_email(db, "ReefBerry test email", "Email alerts from your ReefBerry dashboard are working.")
            flash(request, f"Test email sent to {values['email_to']}. Check your inbox (and spam folder).")
        except NotifyError as e:
            flash(request, str(e), "error")
    elif action == "test_ntfy":
        try:
            send_ntfy(db, "ReefBerry test", "Push alerts from your ReefBerry dashboard are working.", priority="default")
            flash(request, f"Test push sent to topic {topic}.")
        except NotifyError as e:
            flash(request, str(e), "error")
    else:
        flash(request, "Alert settings saved.")
    return RedirectResponse(target, status_code=303)
