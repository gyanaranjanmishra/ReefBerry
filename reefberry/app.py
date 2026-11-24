"""Application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import __version__
from .config import Config, ensure_data_dir, load_config, load_or_create_secret
from .db import Database
from .security import LoginThrottle
from .services.icons import IconLibrary
from .services.controller import Controller
from .services.kasa_service import KasaService, SimulatedKasa
from .web import auth, control, forms, pages, setup
from .web.common import TEMPLATES, Redirect

SESSION_DAYS = 30


def create_app(
    cfg: Config | None = None, kasa: KasaService | None = None, run_controller: bool = True
) -> FastAPI:
    cfg = cfg or load_config()
    ensure_data_dir(cfg)
    db = Database(cfg.db_path)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if run_controller:
            await controller.start()
        yield
        await controller.stop()
        db.close()

    app = FastAPI(
        title="ReefBerry", version=__version__, lifespan=lifespan,
        docs_url=None, redoc_url=None, openapi_url=None,
    )
    app.state.cfg = cfg
    app.state.db = db
    app.state.throttle = LoginThrottle()
    app.state.icons = IconLibrary(cfg.icons_dir)
    TEMPLATES.env.globals["icon"] = app.state.icons.svg
    TEMPLATES.env.globals["icon_names"] = app.state.icons.names
    if kasa is None:
        kasa = SimulatedKasa() if cfg.simulate else KasaService(
            lambda: (db.get_setting("kasa_username", ""), db.get_setting("kasa_password", ""))
        )
    app.state.kasa = kasa
    controller = Controller(db, kasa)
    app.state.controller = controller

    app.add_middleware(
        SessionMiddleware,
        secret_key=load_or_create_secret(cfg),
        session_cookie="reefberry_session",
        max_age=SESSION_DAYS * 24 * 3600,
        same_site="strict",
        https_only=False,  # reached over the home network or Tailscale, which encrypts traffic itself
    )

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(auth.router)
    app.include_router(setup.router)
    app.include_router(forms.router)
    app.include_router(pages.router)
    app.include_router(control.router)

    @app.exception_handler(Redirect)
    async def _redirect(request: Request, exc: Redirect):
        return RedirectResponse(exc.url, status_code=303)

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        page = TEMPLATES.get_template("error.html").render(
            status=exc.status_code, message=exc.detail, version=__version__
        )
        return HTMLResponse(page, status_code=exc.status_code)

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    logging.getLogger(__name__).info("ReefBerry %s using data dir %s", __version__, cfg.data_dir)
    return app
