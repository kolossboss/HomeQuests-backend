from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from starlette.requests import Request

from .config import settings
from .database import Base, engine
from .routers import auth, events, families, points, rewards, tasks

app = FastAPI(title=settings.app_name, version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

Base.metadata.create_all(bind=engine)

with engine.begin() as conn:
    conn.execute(
        text(
            "ALTER TABLE tasks "
            "ADD COLUMN IF NOT EXISTS recurrence_type VARCHAR(16) NOT NULL DEFAULT 'none'"
        )
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


@app.get("/health")
def healthcheck():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "app_name": settings.app_name})
