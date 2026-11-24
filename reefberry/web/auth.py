"""Sign in and sign out."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from ..security import verify_password
from .common import checked_form, get_db, has_users, render, safe_next, sign_in

router = APIRouter()


@router.get("/login")
def login_page(request: Request, next: str = "/"):
    if not has_users(get_db(request)):
        return RedirectResponse("/setup/no-account", status_code=303)
    return render(request, "login.html", next=safe_next(next), error=None, username="")


@router.post("/login")
async def login(request: Request):
    form = await checked_form(request)
    db = get_db(request)
    throttle = request.app.state.throttle
    client = request.client.host if request.client else "unknown"
    username = str(form.get("username", "")).strip()
    next_url = safe_next(str(form.get("next", "/")))

    wait = throttle.blocked_for(client)
    if wait:
        minutes = max(1, round(wait / 60))
        return render(
            request, "login.html", status_code=429, next=next_url, username=username,
            error=f"Too many failed attempts. Try again in about {minutes} minute(s).",
        )

    row = db.one("SELECT id, password_hash FROM users WHERE username = ?", (username,))
    if not row or not verify_password(str(form.get("password", "")), row["password_hash"]):
        throttle.fail(client)
        return render(
            request, "login.html", status_code=401, next=next_url, username=username,
            error="That username and password don't match.",
        )

    throttle.reset(client)
    sign_in(request, row["id"])
    db.log_event("login", detail=username)
    return RedirectResponse(next_url, status_code=303)


@router.post("/logout")
async def logout(request: Request):
    await checked_form(request)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
