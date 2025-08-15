import os, shutil
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Request, Depends, HTTPException, Body
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from jinja2 import Environment, FileSystemLoader, select_autoescape, TemplateNotFound

from sqlalchemy import create_engine, String, Integer, DateTime, Float, ForeignKey, select, func, text
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Mapped, mapped_column, Session
from sqlalchemy.exc import OperationalError

# ------------------------------------------------------------------------------------
# App + static/template setup
# ------------------------------------------------------------------------------------
app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SECRET_KEY", "dev-secret"), same_site="lax")

STATIC_DIR = os.getenv("STATIC_DIR", "static")
TEMPLATES_DIR = os.getenv("TEMPLATES_DIR", "templates")
os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)

# Ensure common static assets are available under /static
for fname in ("style.css", "logo.svg"):
    src = os.path.join(".", fname)
    dst = os.path.join(STATIC_DIR, fname)
    if os.path.exists(src) and not os.path.exists(dst):
        try:
            shutil.copyfile(src, dst)
        except Exception:
            pass

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Jinja across multiple search roots
loader = FileSystemLoader([TEMPLATES_DIR, "app/templates", "."])
env = Environment(loader=loader, autoescape=select_autoescape(["html", "xml"]))
templates = Jinja2Templates(env=env)
templates.env.globals["now"] = lambda: datetime.now(timezone.utc)

# Template candidates
TEMPLATE_INSTRUCTOR_CANDIDATES = [
    "instructor_player_detail.html",  # your file name
    "instructor_players.html",
    "instructor.html",
]
TEMPLATE_PLAYER_CANDIDATES = [
    "dashboard.html",
    "player_dashboard.html",
    "player.html",
]

def render_first_existing(name_list, context):
    last_exc = None
    for name in name_list:
        try:
            return templates.TemplateResponse(name, context)
        except TemplateNotFound as exc:
            last_exc = exc
            continue
    missing = ", ".join(name_list)
    raise HTTPException(status_code=500, detail=f"No template found. Looked for: {missing}. Last error: {last_exc}")

# ------------------------------------------------------------------------------------
# DB + models
# ------------------------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, echo=False, future=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
Base = declarative_base()

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

# Create any missing tables
Base.metadata.create_all(bind=engine)

# --- Lightweight auto-migration for SQLite (adds missing columns) -------------------
def _sqlite_add_missing_columns():
    if engine.url.get_backend_name() != "sqlite":
        return
    with engine.begin() as conn:
        def existing_cols(tbl: str) -> set[str]:
            rows = conn.exec_driver_sql(f"PRAGMA table_info({tbl})").all()
            return {r[1] for r in rows}  # name is 2nd column

        # players
        cols = existing_cols("players")
        want = {
            "age_group": "age_group VARCHAR(16)",
            "photo_url": "photo_url VARCHAR(400)",
            "login_code": "login_code VARCHAR(32)"
        }
        for col, ddl in want.items():
            if col not in cols:
                conn.exec_driver_sql(f"ALTER TABLE players ADD COLUMN {ddl}")

        # instructors
        cols = existing_cols("instructors")
        if "login_code" not in cols:
            conn.exec_driver_sql("ALTER TABLE instructors ADD COLUMN login_code VARCHAR(32)")

# run migration once on import
try:
    _sqlite_add_missing_columns()
except Exception as e:
    # Don’t kill startup if migration hiccups; errors will still surface in logs.
    print("SQLite migration warning:", e)

def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ------------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------------
AGE_BUCKETS = ["7-9", "10-12", "13-15", "16-18", "18+"]

def ensure_instructor_in_session(request: Request, db: Session) -> int:
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

# ------------------------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------------------------
@app.head("/")
def head_root():
    return PlainTextResponse("OK")

@app.get("/", response_class=HTMLResponse)
def root():
    return RedirectResponse("/instructor", status_code=302)

@app.get("/healthz")
def healthz():
    return {"ok": True}

@app.get("/instructor", response_class=HTMLResponse)
def instructor_view(request: Request, db: Session = Depends(get_db)):
    instructor_id = ensure_instructor_in_session(request, db)

    # In case the DB existed prior to migration, try-with-fallback
    try:
        rows = db.execute(select(Player.id, Player.name, Player.age_group, Player.photo_url)).all()
        include_age = True
    except OperationalError:
        rows = db.execute(select(Player.id, Player.name, Player.photo_url)).all()
        include_age = False

    players = []
    for row in rows:
        if include_age:
            pid, name, age, purl = row
            age = age or ""
        else:
            # legacy DB had no age_group column
            pid, name, purl = row
            age = ""
        players.append({"id": pid, "name": name, "age_group": age, "photo_url": purl, "starred": False})

    starred = starred_ids_for_instructor(db, instructor_id)
    for p in players:
        p["starred"] = p["id"] in starred

    grouped = {k: [] for k in AGE_BUCKETS}
    for p in players:
        key = p["age_group"] if p["age_group"] in grouped else AGE_BUCKETS[-1]
        grouped[key].append(p)

    my_clients = [p for p in players if p["starred"]]

    ctx = {
        "request": request,
        "players": players,
        "clients": players,          # alias
        "roster": players,           # alias for older templates
        "grouped": grouped,
        "my_clients": my_clients,
        "favorites": my_clients,     # alias
        "age_buckets": AGE_BUCKETS,
        "instructor_id": instructor_id,
        "flash": request.session.get("flash"),
    }
    return render_first_existing(TEMPLATE_INSTRUCTOR_CANDIDATES, ctx)

@app.post("/api/players/{player_id}/toggle_star")
def toggle_star(player_id: int, request: Request, payload: dict = Body(None), db: Session = Depends(get_db)):
    instructor_id = (payload or {}).get("instructor_id") or request.session.get("instructor_id")
    if not instructor_id:
        raise HTTPException(status_code=400, detail="instructor_id required")
    if not db.get(Player, player_id):
        raise HTTPException(status_code=404, detail="Player not found")

    existing = db.get(Favorite, {"instructor_id": instructor_id, "player_id": player_id})
    if existing:
        db.delete(existing); db.commit()
        return {"starred": False}
    else:
        db.add(Favorite(instructor_id=instructor_id, player_id=player_id)); db.commit()
        return {"starred": True}

@app.get("/player/{player_id}", response_class=HTMLResponse)
def player_dashboard(player_id: int, request: Request, db: Session = Depends(get_db)):
    player = db.get(Player, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    # Exit velocity series (smaller chart, red line handled in template/JS)
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

    ctx = {
        "request": request,
        "player": {"id": player.id, "name": player.name, "photo_url": player.photo_url, "login_code": player.login_code},
        "chart_labels": labels,
        "chart_values": values,
        "coach_notes": coach_notes,
        "drills": drills,
        "flash": request.session.get("flash"),
    }
    return render_first_existing(TEMPLATE_PLAYER_CANDIDATES, ctx)

# Seed for quick testing
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
    p = db.scalar(select(Player).order_by(Player.id.asc()))
    if p and not db.scalar(select(func.count()).select_from(ExitMetric).where(ExitMetric.player_id == p.id)):
        now = datetime.now(timezone.utc)
        for i, v in enumerate([68.2, 70.1, 72.5, 74.0, 75.8]):
            db.add(ExitMetric(player_id=p.id, value=v, created_at=now.replace(hour=12, minute=i)))
        db.commit()
    return {"ok": True}

# Friendly global 500 for Render logs
@app.exception_handler(Exception)
async def all_exception_handler(request: Request, exc: Exception):
    import traceback
    print("----- Unhandled Exception -----")
    print(traceback.format_exc())
    return PlainTextResponse("Internal Server Error", status_code=500)
