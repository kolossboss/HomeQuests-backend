from __future__ import annotations

from pathlib import Path
import asyncio
import logging
import re
from uuid import uuid4
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from starlette.requests import Request

from .achievement_engine import ensure_achievement_catalog
from .config import settings
from .database import Base, SessionLocal, engine
from .maintenance import penalty_worker, push_worker
from .migrations import run_migrations
from .notification_dispatcher import start_remote_dispatcher, stop_remote_dispatcher
from .push_notifications import close_notification_clients
from .routers import achievements, auth, events, families, live, points, push, rewards, system, tasks

logger = logging.getLogger(__name__)


def _warn_about_insecure_defaults() -> None:
    if settings.secret_key in {"change-me-in-production", "CHANGE_THIS_SECRET"}:
        logger.warning("SECRET_KEY verwendet noch einen Platzhalter. Bitte in Produktion ersetzen.")
    if "homequests:homequests@" in settings.database_url:
        logger.warning("DATABASE_URL verwendet Standard-Zugangsdaten. Bitte produktive Zugangsdaten setzen.")
    if settings.access_token_expire_minutes > 60 * 24 * 90:
        logger.warning("ACCESS_TOKEN_EXPIRE_MINUTES ist sehr hoch gesetzt (%s Minuten).", settings.access_token_expire_minutes)


def initialize_database() -> None:
    try:
        Base.metadata.create_all(bind=engine)
        run_migrations(engine)
        with SessionLocal() as db:
            ensure_achievement_catalog(db)
            db.commit()
    except OperationalError as exc:
        raise RuntimeError(
            "Datenbankverbindung fehlgeschlagen. "
            "Prüfe DATABASE_URL und den Host. "
            "In Docker-Compose muss der DB-Service erreichbar sein (Standard-Host: 'db')."
        ) from exc


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    _warn_about_insecure_defaults()
    start_remote_dispatcher()
    penalty_task = None
    push_task = None
    if settings.penalty_worker_enabled:
        penalty_task = asyncio.create_task(penalty_worker(), name="homequests-penalty-worker")
    if settings.push_worker_enabled:
        push_task = asyncio.create_task(push_worker(), name="homequests-push-worker")
    try:
        yield
    finally:
        if penalty_task is not None:
            penalty_task.cancel()
            with suppress(asyncio.CancelledError):
                await penalty_task
        if push_task is not None:
            push_task.cancel()
            with suppress(asyncio.CancelledError):
                await push_task
        stop_remote_dispatcher()
        close_notification_clients()


app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)


@app.middleware("http")
async def request_context(request: Request, call_next):
    supplied = (request.headers.get("x-request-id") or "").strip()
    request_id = supplied if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", supplied) else uuid4().hex
    request.state.request_id = request_id
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "Unbehandelter API-Fehler (request_id=%s, method=%s, path=%s)",
            request_id,
            request.method,
            request.url.path,
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Interner Serverfehler", "request_id": request_id},
            headers={"X-Request-ID": request_id},
        )
    response.headers["X-Request-ID"] = request_id
    return response

cors_allow_origins = settings.cors_allow_origins or []
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allow_origins,
    allow_credentials="*" not in cors_allow_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

base_dir = Path(__file__).parent
static_dir = base_dir / "web" / "static"
templates_dir = base_dir / "web" / "templates"

app.mount("/static", StaticFiles(directory=static_dir), name="static")
templates = Jinja2Templates(directory=str(templates_dir))

app.include_router(auth.router)
app.include_router(families.router)
app.include_router(achievements.router)
app.include_router(tasks.router)
app.include_router(events.router)
app.include_router(rewards.router)
app.include_router(points.router)
app.include_router(live.router)
app.include_router(system.router)
app.include_router(push.router)


@app.get("/health")
def healthcheck():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "version": settings.app_version, "database": "unavailable"},
        )
    return {"status": "ok", "version": settings.app_version, "database": "ok"}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"app_name": settings.app_name},
    )
