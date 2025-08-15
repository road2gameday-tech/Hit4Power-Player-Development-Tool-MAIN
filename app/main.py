import os, shutil
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import FastAPI, Request, Depends, HTTPException, Body
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from jinja2 import Environment, FileSystemLoader, select_autoescape

from sqlalchemy import (
    create_engine, String, Integer, DateTime, Float, ForeignKey, select, func
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Mapped, mapped_column, Session

# -----------------------------------------------------------------------------
# App & templating (robust paths + assets copied to /static)
# -----------------------------------------------------------------------------
app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SECRET_KEY", "dev-secret"), same_site="lax")

STATIC_DIR = os.getenv("STATIC_DIR", "static")
TEMPLATES_DIR = os.getenv("TEMPLATES_DIR", "templates")
os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)

# make sure common files are available from /static
for fname in ("style.css", "logo.svg"):
    src = os.path.join(".", fname)
    dst = os.path.join(STATIC_DIR, fname)
    if os.path.exists(src) and not os.path.exists(dst):
        try:
            shutil.copyfile(src, dst)
        except Exception:
            pass

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Jinja that searches multiple folders (templates/, app/templates/, and repo root)
loader = FileSystemLoader([TEMPLATES_DIR, "app/templates", "."])
env = Environment(loader=loader, autoescape=select_autoescape(["html", "xml"]))
templates = Jinja2Templates(env=env)
templates.env.globals["now"] = lambda: datetime.now(timezone.utc)

# -----------------------------------------------------------------------------
# Database
# -----------------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, echo=False, future=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
Base = declarative_base()

# -----------------------------------------------------------------------------
# Models
# -----------------------------------------------------------------------------
class Instructor(Base):
    __tablename__ = "instructors"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), default="Coach")
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


Base.metadata.create_all(bind=engine)

# -----------------------------------------------------------------------------
# DB dependency
# -----------------------------------------------------------------------------
def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
AGE_BUCKETS = ["7-9", "10-12", "13-15", "16-18", "18+"]

def ensure_instructor_in_session(request: Request, db: Session) -> int:
    """Guarantee there's an instructor_id in session so starring works immediately."""
    iid = request.session.get("instructor_id")
    if iid:
        return iid
    inst = db.scalar(select(Instructor).where(Instructor.login_code == "DEFAULT"))
    if not inst:
        inst = Instructor(name="Coach", login_code="DEFAULT")
        db.add(inst); db.commit(); db.refresh(inst)
    request.session["instructor_id"] = inst.id
    return inst.id

def starred_ids_for_instructor(db: Session, instructor_id: Optional[int]) -> set[int]:
    if not instructor_id:
        return set()
    rows = db.execute(select(Favorite.player_id).where(Favorite.instructor_id == instructor_id)).all()
    return {r[0] for r in rows}

# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def root():
    return RedirectResponse("/instructor", status_code=302)

@app.get("/healthz")
def healthz():
    return {"ok": True}

@app.get("/instructor", response_class=HTMLResponse)
def instructor_clients(request: Request, db: Session = Depends(get_db)):
    # make sure we have someone in session so ⭐ works
    instructor_id = ensure_instructor_in_session(request, db)

    rows = db.execute(select(Player.id, Player.name, Player.age_group, Player.photo_url)).all()
    players = [{"id": pid, "name": name, "age_group": age or "", "photo_url": purl, "starred": False}
               for (pid, name, age, purl) in rows]

    starred = starred_ids_for_instructor(db, instructor_id)
    for p in players:
        p["starred"] = p["id"] in starred

    # build grouped map for templates that show columns per age group
    grouped = {k: [] for k in AGE_BUCKETS}
    for p in players:
        key = p["age_group"] if p["age_group"] in grouped else AGE_BUCKETS[-1]  # default to 18+
        grouped[key].append(p)

    my_clients = [p for p in players if p["starred"]]

    ctx = {
        "request": request,
        "players": players,
        "grouped": grouped,
        "my_clients": my_clients,
        "age_buckets": AGE_BUCKETS,
        "instructor_id": instructor_id,
        "flash": request.session.get("flash"),
    }
    return templates.TemplateResponse("instructor_players.html", ctx)

@app.post("/api/players/{player_id}/toggle_star")
def toggle_star(player_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    instructor_id = payload.get("instructor_id")
    if not instructor_id:
        raise HTTPException(status_code=400, detail="instructor_id required")
    instr = db.get(Instructor, instructor_id)
    if not instr:
        raise HTTPException(status_code=404, detail="Instructor not found")
    player = db.get(Player, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    existing = db.get(Favorite, {"instructor_id": instructor_id, "player_id": player_id})
    if existing:
        db.delete(existing)
        db.commit()
        return {"starred": False}
    else:
        fav = Favorite(instructor_id=instructor_id, player_id=player_id)
        db.add(fav); db.commit()
        return {"starred": True}

@app.get("/player/{player_id}", response_class=HTMLResponse)
def player_dashboard(player_id: int, request: Request, db: Session = Depends(get_db)):
    player = db.get(Player, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    data_rows = db.execute(
        select(ExitMetric.value, ExitMetric.created_at)
        .where(ExitMetric.player_id == player_id)
        .order_by(ExitMetric.created_at.asc())
        .limit(100)
    ).all()
    labels = [r[1].strftime("%m/%d") if isinstance(r[1], datetime) else str(r[1]) for r in data_rows]
    values = [float(r[0]) for r in data_rows]

    notes = db.execute(
        select(CoachNote.coach_name, CoachNote.text, CoachNote.created_at)
        .where(CoachNote.player_id == player_id)
        .order_by(CoachNote.created_at.desc())
    ).all()
    coach_notes = [{"coach_name": n[0], "text": n[1], "created_at": n[2].strftime("%Y-%m-%d %H:%M")} for n in notes]

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

# Simple seed so you have data quickly (remove in prod)
@app.post("/dev/seed")
def dev_seed(db: Session = Depends(get_db)):
    if not db.scalar(select(func.count()).select_from(Instructor)):
        db.add(Instructor(name="Coach Demo", login_code="DEFAULT"))
    if not db.scalar(select(func.count()).select_from(Player)):
        db.add_all([
            Player(name="Ava Martinez", age_group="13-15"),
            Player(name="Liam Johnson", age_group="10-12"),
            Player(name="Mia Lee", age_group="16-18"),
        ])
    db.commit()
    # add a few exit metrics for first player
    p = db.scalar(select(Player).order_by(Player.id.asc()))
    if p and not db.scalar(select(func.count()).select_from(ExitMetric).where(ExitMetric.player_id == p.id)):
        now = datetime.now(timezone.utc)
        for i, v in enumerate([68.2, 70.1, 72.5, 74.0, 75.8]):
            db.add(ExitMetric(player_id=p.id, value=v, created_at=now.replace(hour=12, minute=i)))
        db.commit()
    return {"ok": True}

# Catch-all 500 that logs to server (for Render “Internal Server Error” screens)
@app.exception_handler(Exception)
async def all_exception_handler(request: Request, exc: Exception):
    import traceback
    print("----- Unhandled Exception -----")
    print(traceback.format_exc())
    return PlainTextResponse("Internal Server Error", status_code=500)
