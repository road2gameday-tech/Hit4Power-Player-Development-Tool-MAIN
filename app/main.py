import os
from datetime import datetime
from typing import Any, Dict, List

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.templating import Jinja2Templates

# --- App & Templates ---------------------------------------------------------
app = FastAPI()

# Sessions (templates use {{ session.get(...) }})
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

# Serve /static (CSS/JS/images)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")

# Site links configurable via env (used by header buttons)
INSTAGRAM_URL = os.getenv("INSTAGRAM_URL", "https://instagram.com/hit4power")
WEBSITE_URL = os.getenv("WEBSITE_URL", "https://hit4power.com")

# Make helpers/globals available in Jinja
templates.env.globals.update(
    now=datetime.utcnow,        # enables {{ now().year }}
    instagram_url=INSTAGRAM_URL,
    website_url=WEBSITE_URL,
)

# Small render helper: always inject request & session
def render(name: str, request: Request, **ctx: Dict[str, Any]) -> HTMLResponse:
    base_ctx = {
        "request": request,
        "session": getattr(request, "session", {}),
    }
    base_ctx.update(ctx)
    return templates.TemplateResponse(name, base_ctx)

# --- Data access (lightweight, resilient) ------------------------------------
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def fetch_players(db) -> List[Dict[str, Any]]:
    """
    Read only the columns we actually need so schema drift
    (like a missing instructor_id column) won't 500 the page.
    """
    try:
        rows = db.execute(
            text("SELECT id, name, photo_url, phone FROM players")
        ).mappings().all()
        return [
            {
                "id": r.get("id"),
                "name": r.get("name") or "Player",
                "photo_url": r.get("photo_url") or "",
                "phone": r.get("phone") or "",
            }
            for r in rows
        ]
    except Exception:
        # Fail soft: show an empty dashboard rather than crashing
        return []

# --- Routes -------------------------------------------------------------------
# Keep current behavior: root -> /instructor (change if you want a true homepage)
@app.get("/", response_class=HTMLResponse)
def root():
    return RedirectResponse("/instructor", status_code=302)

@app.get("/instructor", response_class=HTMLResponse)
def instructor_view(request: Request, db=Depends(get_db)):
    players = fetch_players(db)
    ctx = {
        "title": "Instructor",
        "players": players,
    }
    return render("dashboard.html", request, **ctx)

@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)
