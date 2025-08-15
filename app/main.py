import os
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import FastAPI, Request, Depends, HTTPException, Form, Body
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel

from sqlalchemy import (
    create_engine, String, Integer, DateTime, Float, Boolean, ForeignKey, UniqueConstraint, select, func
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Mapped, mapped_column, Session

# --------------------------------------------------------------------------------------
# Basic app setup
# --------------------------------------------------------------------------------------
app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SECRET_KEY", "dev-secret"), same_site="lax")

# Static & templates
STATIC_DIR = os.getenv("STATIC_DIR", "static")
TEMPLATES_DIR = os.getenv("TEMPLATES_DIR", "templates")
os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)
# Provide a simple "now()" in templates (used in footers)
templates.env.globals["now"] = lambda: datetime.now(timezone.utc)

# --------------------------------------------------------------------------------------
# Database (SQLite by default; set DATABASE_URL env for Postgres etc.)
# --------------------------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, echo=False, future=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
Base = declarative_base()

# --------------------------------------------------------------------------------------
# Models (minimal set used by the pages/routes below)
# --------------------------------------------------------------------------------------
class Instructor(Base):
    __tablename__ = "instructors"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), default="Coach")
    # simple auth via login code if you need it
    login_code: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)


class Player(Base):
    __tablename__ = "players"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    age_group: Mapped[Optional[str]] = mapped_column(String(16))
    photo_url: Mapped[Optional[str]] = mapped_column(String(400))
    login_code: Mapped[Optional[str]] = mapped_column(String(32))


class Favorite(Base):
    __tablename__ = "favorites"
    instructor_id: Mapped[int] = mapped_column(ForeignKey("instructors.id"), primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    instructor = relationship("Instructor")
    player = relationship("Player")


class ExitMetric(Base):
    __tablename__ = "exit_metrics"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    value: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    player = relationship("Player")


class CoachNote(Base):
    __tablename__ = "coach_notes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    coach_name: Mapped[str] = mapped_column(String(120))
    text: Mapped[str] = mapped_column(String(4000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Drill(Base):
    __tablename__ = "drills"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    video_url: Mapped[Optional[str]] = mapped_column(String(600))


class DrillAssignment(Base):
    __tablename__ = "drill_assignments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    drill_id: Mapped[int] = mapped_column(ForeignKey("drills.id"))
    note: Mapped[Optional[str]] = mapped_column(String(1000))

    player = relationship("Player")
    drill = relationship("Drill")

# Create tables if they don't exist
Base.metadata.create_all(bind=engine)

# --------------------------------------------------------------------------------------
# DB dependency
# --------------------------------------------------------------------------------------
def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------
def get_instructor_id_from_session(request: Request, db: Session) -> Optional[int]:
    """Try session, else None."""
    try:
        return request.session.get("instructor_id")
    except Exception:
        return None

def starred_ids_for_instructor(db: Session, instructor_id: Optional[int]) -> set[int]:
    if not instructor_id:
        return set()
    rows = db.execute(select(Favorite.player_id).where(Favorite.instructor_id == instructor_id)).all()
    return {r[0] for r in rows}

# --------------------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    return RedirectResponse(url="/instructor", status_code=302)

@app.get("/instructor", response_class=HTMLResponse)
def instructor_clients(request: Request, db: Session = Depends(get_db)):
    """
    Render the Clients page the template expects:
    - players: [{id, name, age_group, photo_url, starred}]
    - instructor_id: current instructor (or None if not logged in)
    """
    # If you have real auth, set request.session["instructor_id"] at login time.
    instructor_id = get_instructor_id_from_session(request, db)

    players = db.execute(select(Player.id, Player.name, Player.age_group, Player.photo_url)).all()
    players = [{"id": pid, "name": name, "age_group": age, "photo_url": purl, "starred": False}
               for (pid, name, age, purl) in players]

    starred = starred_ids_for_instructor(db, instructor_id)
    for p in players:
        p["starred"] = p["id"] in starred

    return templates.TemplateResponse(
        "instructor_players.html",
        {"request": request, "players": players, "instructor_id": instructor_id, "flash": request.session.get("flash")}
    )

class ToggleStarBody(BaseModel):
    instructor_id: int

@app.post("/api/players/{player_id}/toggle_star")
def toggle_star(player_id: int, payload: ToggleStarBody = Body(...), db: Session = Depends(get_db)):
    """
    Toggle a player's 'favorite' for an instructor.
    Returns: { "starred": true/false }
    """
    # validate player/instructor exist
    instr = db.get(Instructor, payload.instructor_id)
    if not instr:
        raise HTTPException(status_code=404, detail="Instructor not found")
    player = db.get(Player, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    existing = db.get(Favorite, {"instructor_id": payload.instructor_id, "player_id": player_id})
    if existing:
        db.delete(existing)
        db.commit()
        return {"starred": False}
    else:
        fav = Favorite(instructor_id=payload.instructor_id, player_id=player_id)
        db.add(fav)
        db.commit()
        return {"starred": True}

@app.get("/player/{player_id}", response_class=HTMLResponse)
def player_dashboard(player_id: int, request: Request, db: Session = Depends(get_db)):
    """
    Render the player dashboard (used by /instructor page links).
    Provides:
      - player
      - chart_labels & chart_values for Exit Velocity (small red line in UI)
      - coach_notes (latest first)
      - drills (assigned with optional note)
    """
    player = db.get(Player, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    # Exit Velocity series (last 20 by time)
    data_rows = db.execute(
        select(ExitMetric.value, ExitMetric.created_at)
        .where(ExitMetric.player_id == player_id)
        .order_by(ExitMetric.created_at.asc())
        .limit(100)
    ).all()
    labels = [r[1].strftime("%m/%d") if isinstance(r[1], datetime) else str(r[1]) for r in data_rows]
    values = [float(r[0]) for r in data_rows]

    # Coach notes
    notes = db.execute(
        select(CoachNote.coach_name, CoachNote.text, CoachNote.created_at)
        .where(CoachNote.player_id == player_id)
        .order_by(CoachNote.created_at.desc())
    ).all()
    coach_notes = [{"coach_name": n[0], "text": n[1], "created_at": n[2].strftime("%Y-%m-%d %H:%M")} for n in notes]

    # Assigned drills
    drills_rows = db.execute(
        select(Drill.title, Drill.video_url, DrillAssignment.note)
        .join(DrillAssignment, Drill.id == DrillAssignment.drill_id)
        .where(DrillAssignment.player_id == player_id)
        .order_by(DrillAssignment.id.desc())
    ).all()
    drills = [{"title": d[0], "video_url": d[1], "note": d[2]} for d in drills_rows]

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "player": {"id": player.id, "name": player.name, "photo_url": player.photo_url, "login_code": player.login_code},
            "chart_labels": labels,
            "chart_values": values,
            "coach_notes": coach_notes,
            "drills": drills,
            "flash": request.session.get("flash"),
        },
    )

# --------------------------------------------------------------------------------------
# (Optional) Lightweight seed route so you have something to click during testing.
# Remove in production.
# --------------------------------------------------------------------------------------
@app.post("/dev/seed")
def dev_seed(db: Session = Depends(get_db)):
    if not db.scalar(select(func.count()).select_from(Instructor)):
        db.add(Instructor(name="Coach Demo", login_code="COACH1"))
    if not db.scalar(select(func.count()).select_from(Player)):
        players = [
            Player(name="Ava Martinez", age_group="13-15"),
            Player(name="Liam Johnson", age_group="10-12"),
            Player(name="Mia Lee", age_group="16-18"),
        ]
        db.add_all(players)
    db.commit()
    # add a few exit metrics for first player
    p = db.scalar(select(Player).order_by(Player.id.asc()))
    if p and not db.scalar(select(func.count()).select_from(ExitMetric).where(ExitMetric.player_id == p.id)):
        now = datetime.now(timezone.utc)
        for i, v in enumerate([68.2, 70.1, 72.5, 74.0, 75.8]):
            db.add(ExitMetric(player_id=p.id, value=v, created_at=now.replace(hour=12, minute=i)))
        db.commit()
    return {"ok": True}
