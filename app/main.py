# app/main.py
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Generator, List, Dict, Any

from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.templating import Jinja2Templates

from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker, Session

# -----------------------------------------------------------------------------
# Paths & Templates
# -----------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

app = FastAPI()

# Session (use your own strong SECRET_KEY in env on Render)
SECRET_KEY = os.getenv("SECRET_KEY", "dev-insecure-change-me")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

# Static files
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
# Make a 'now()' helper available to all Jinja templates
templates.env.globals["now"] = datetime.utcnow

# -----------------------------------------------------------------------------
# Database
# -----------------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    # Render uses multiple threads; SQLite needs this flag
    connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def render(name: str, request: Request, **ctx) -> HTMLResponse:
    """Standard template renderer that always passes session + current_year."""
    base = {
        "request": request,
        "session": request.session,
        "current_year": datetime.utcnow().year,
    }
    base.update(ctx)
    return templates.TemplateResponse(name, base)

def list_players(db: Session) -> List[Dict[str, Any]]:
    """
    Return a list of players as dictionaries, selecting only columns that actually
    exist in the 'players' table to avoid OperationalError on Render.
    """
    inspector = inspect(engine)
    tables = set()
    try:
        tables = set(inspector.get_table_names())
    except Exception:
        # If reflection fails for any reason, fall back to safe empty state.
        return []

    if "players" not in tables:
        return []

    try:
        cols = {col["name"] for col in inspector.get_columns("players")}
    except Exception:
        cols = {"id", "name"}  # minimal fallback

    # We only try to select from this safe, optional set:
    preferred = ["id", "name", "photo_url", "phone"]
    select_cols = [c for c in preferred if c in cols]
    if not select_cols:
        # table exists but has none of our expected columns; give up safely
        return []

    sql = f"SELECT {', '.join(select_cols)} FROM players ORDER BY name"
    try:
        rows = db.execute(text(sql)).mappings().all()
    except Exception:
        # If anything goes wrong, fail closed (no crash)
        return []

    players: List[Dict[str, Any]] = []
    for row in rows:
        # row is a RowMapping; use get with defaults
        players.append({
            "id": row.get("id"),
            "name": row.get("name") or (f"Player {row.get('id')}" if row.get("id") else "Player"),
            "photo_url": row.get("photo_url") or "/static/img/avatar-placeholder.png",
            "phone": row.get("phone") or "",
        })
    return players

# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
@app.get("/healthz")
def healthz():
    return JSONResponse({"ok": True, "time": datetime.utcnow().isoformat()})

from fastapi import Request
from fastapi.responses import HTMLResponse

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return render("home.html", request)  # uses your existing render() helper


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/")

@app.get("/instructor", response_class=HTMLResponse)
def instructor_view(request: Request, db: Session = Depends(get_db)):
    # Mark the session so templates can toggle instructor-specific UI
    request.session["role"] = "instructor"

    players = list_players(db)

    ctx = {
        "page_title": "Instructor Dashboard",
        "players": players,
    }
    # Render dashboard.html (ensure your template is the instructor version)
    return render("dashboard.html", request, **ctx)

# Optional: detail page (kept minimal so links like /instructor/player/{id} won't 404)
@app.get("/instructor/player/{player_id}", response_class=HTMLResponse)
def instructor_player_detail(player_id: int, request: Request, db: Session = Depends(get_db)):
    request.session["role"] = "instructor"

    # Fetch minimal player info safely
    players = list_players(db)
    player = next((p for p in players if p.get("id") == player_id), None)

    if not player:
        # If you have a dedicated 404 template, you can render it instead.
        return render("dashboard.html", request, page_title="Instructor Dashboard", players=players)

    # If you have a specialized template (e.g., instructor_player_detail.html), render it here.
    # Falling back to dashboard until that template exists in your repo.
    return render("dashboard.html", request, page_title=f"{player.get('name')} • Instructor", players=players)

# -----------------------------------------------------------------------------
# Error handlers (basic, optional)
# -----------------------------------------------------------------------------
@app.exception_handler(500)
def server_error(request: Request, exc: Exception):
    # Avoid leaking stack traces in production; log via Render logs instead.
    return render("dashboard.html", request, page_title="Something went wrong", players=[])
