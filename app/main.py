from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from starlette.requests import Request

from .config import settings
from .database import Base, engine
from .routers import auth, events, families, live, points, rewards, tasks

app = FastAPI(title=settings.app_name, version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

try:
    Base.metadata.create_all(bind=engine)

    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE tasks "
                "ADD COLUMN IF NOT EXISTS recurrence_type VARCHAR(16) NOT NULL DEFAULT 'none'"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE tasks "
                "ADD COLUMN IF NOT EXISTS reminder_offsets_minutes JSON NOT NULL DEFAULT '[]'"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE tasks "
                "ADD COLUMN IF NOT EXISTS active_weekdays JSON NOT NULL DEFAULT '[0,1,2,3,4,5,6]'"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE tasks "
                "ADD COLUMN IF NOT EXISTS special_template_id INTEGER NULL"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE tasks "
                "ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE tasks "
                "ADD COLUMN IF NOT EXISTS penalty_enabled BOOLEAN NOT NULL DEFAULT FALSE"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE tasks "
                "ADD COLUMN IF NOT EXISTS penalty_points INTEGER NOT NULL DEFAULT 0"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE tasks "
                "ADD COLUMN IF NOT EXISTS penalty_last_applied_at TIMESTAMP NULL"
            )
        )
        if engine.dialect.name == "postgresql":
            conn.execute(
                text(
                    "ALTER TABLE users "
                    "ALTER COLUMN email DROP NOT NULL"
                )
            )
            conn.execute(
                text(
                    """
                    DO $$
                    BEGIN
                        IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'specialtaskintervalenum') THEN
                            IF NOT EXISTS (
                                SELECT 1
                                FROM pg_enum e
                                JOIN pg_type t ON t.oid = e.enumtypid
                                WHERE t.typname = 'specialtaskintervalenum' AND e.enumlabel = 'monthly'
                            ) THEN
                                ALTER TYPE specialtaskintervalenum ADD VALUE 'monthly';
                            END IF;
                        END IF;
                    END $$;
                    """
                )
            )
            conn.execute(
                text(
                    """
                    DO $$
                    BEGIN
                        IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'pointssourceenum') THEN
                            IF NOT EXISTS (
                                SELECT 1
                                FROM pg_enum e
                                JOIN pg_type t ON t.oid = e.enumtypid
                                WHERE t.typname = 'pointssourceenum' AND e.enumlabel = 'reward_contribution'
                            ) THEN
                                ALTER TYPE pointssourceenum ADD VALUE 'reward_contribution';
                            END IF;
                            IF NOT EXISTS (
                                SELECT 1
                                FROM pg_enum e
                                JOIN pg_type t ON t.oid = e.enumtypid
                                WHERE t.typname = 'pointssourceenum' AND e.enumlabel = 'task_penalty'
                            ) THEN
                                ALTER TYPE pointssourceenum ADD VALUE 'task_penalty';
                            END IF;
                        END IF;
                    END $$;
                    """
                )
            )
except OperationalError as exc:
    raise RuntimeError(
        "Datenbankverbindung fehlgeschlagen. "
        "Prüfe DATABASE_URL und den Host. "
        "In Docker-Compose muss der DB-Service erreichbar sein (Standard-Host: 'db')."
    ) from exc

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


@app.get("/health")
def healthcheck():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "app_name": settings.app_name})
