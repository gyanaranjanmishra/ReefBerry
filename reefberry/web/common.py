"""Shared helpers for the web routes: templates, auth guards, CSRF-checked forms, flash messages."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates
from starlette.datastructures import FormData

from .. import __version__
from ..db import Database
from ..security import csrf_ok, csrf_token
from ..services.kasa_service import ROLES, KasaService

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
TEMPLATES.env.globals["ROLES"] = ROLES
TEMPLATES.env.globals["version"] = __version__


class Redirect(Exception):
    """Raised by guards; turned into a 303 redirect by the app's exception handler."""

    def __init__(self, url: str):
        self.url = url


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_kasa(request: Request) -> KasaService:
    return request.app.state.kasa


def has_users(db: Database) -> bool:
    return db.one("SELECT 1 FROM users LIMIT 1") is not None


def current_user(request: Request) -> dict | None:
    uid = request.session.get("uid")
    if not uid:
        return None
    row = get_db(request).one("SELECT id, username, session_epoch FROM users WHERE id = ?", (uid,))
    # A password change or reset bumps session_epoch, which signs out older sessions.
    if not row or request.session.get("epoch") != row["session_epoch"]:
        return None
    return dict(row)


def sign_in(request: Request, user_id: int) -> None:
    epoch = get_db(request).one("SELECT session_epoch FROM users WHERE id = ?", (user_id,))["session_epoch"]
    request.session.clear()
    request.session["uid"] = user_id
    request.session["epoch"] = epoch


def require_user(request: Request) -> dict:
    db = get_db(request)
    if not has_users(db):
        raise Redirect("/setup/no-account")
    user = current_user(request)
    if not user:
        raise Redirect(f"/login?next={quote(request.url.path)}")
    return user


def require_setup_done(request: Request) -> dict:
    user = require_user(request)
    if not get_db(request).get_setting("setup_complete", False):
        raise Redirect("/setup")
    return user


def require_api_user(request: Request) -> dict:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in first.")
    return user


async def checked_form(request: Request) -> FormData:
    form = await request.form()
    if not csrf_ok(request, form.get("csrf")):
        raise HTTPException(
            status_code=400, detail="This form expired. Go back, reload the page and try again."
        )
    return form


def flash(request: Request, message: str, kind: str = "ok") -> None:
    request.session.setdefault("flash", []).append({"kind": kind, "text": message})


def render(request: Request, name: str, status_code: int = 200, **context):
    db = get_db(request)
    base = {
        "csrf": csrf_token(request),
        "user": current_user(request),
        "tank_name": db.get_setting("tank_name") or "My reef",
        "flashes": request.session.pop("flash", []),
        "path": request.url.path,
    }
    base.update(context)
    return TEMPLATES.TemplateResponse(request, name, base, status_code=status_code)


def safe_next(target: str | None, default: str = "/") -> str:
    """Only allow redirects to paths on this site."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return default
