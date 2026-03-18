from pathlib import Path
import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import OperationalError
from starlette.requests import Request

from .config import settings
from .database import Base, engine
from .maintenance import penalty_worker, push_worker
from .migrations import run_migrations
from .notification_dispatcher import start_remote_dispatcher, stop_remote_dispatcher
from .routers import auth, events, families, live, points, push, rewards, system, tasks

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
        stop_remote_dispatcher()
        if penalty_task is not None:
            penalty_task.cancel()
            with suppress(asyncio.CancelledError):
                await penalty_task
        if push_task is not None:
            push_task.cancel()
            with suppress(asyncio.CancelledError):
                await push_task


app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)

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
app.include_router(tasks.router)
app.include_router(events.router)
app.include_router(rewards.router)
app.include_router(points.router)
app.include_router(live.router)
app.include_router(system.router)
app.include_router(push.router)


@app.get("/health")
def healthcheck():
    return {"status": "ok", "version": settings.app_version}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "app_name": settings.app_name})
