# app/main.py
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, Request, Depends, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from starlette.middleware.sessions import SessionMiddleware
from starlette.status import HTTP_302_FOUND
from jinja2 import TemplateNotFound

from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, ForeignKey, Text, select, func, UniqueConstraint
)
from sqlalchemy.orm import sessionmaker, declarative_base, Session, relationship

# ------------------------------------------------------------------------------
# Config
# ------------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
UPLOADS_DIR = BASE_DIR / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")
SECRET_KEY = os.getenv("SESSION_SECRET", "dev-secret-change-me")

TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM = os.getenv("TWILIO_FROM_NUMBER")

# ------------------------------------------------------------------------------
# App + Middleware
# ------------------------------------------------------------------------------
app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

# Static files (style.css, logo.svg, etc.)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Templates
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# ------------------------------------------------------------------------------
# DB setup
# ------------------------------------------------------------------------------
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

# ------------------------------------------------------------------------------
# Models
# ------------------------------------------------------------------------------
class Instructor(Base):
    __tablename__ = "instructors"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    login_code = Column(String(64), unique=True, nullable=False)

    players = relationship("Player", back_populates="instructor")


class Player(Base):
    __tablename__ = "players"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    login_code = Column(String(64), unique=True, nullable=False)
    phone = Column(String(32), nullable=True)
    photo_url = Column(String(500), nullable=True)
    # age_group is OPTIONAL in DB; don't select it unless it exists
    # age_group = Column(String(32))

    instructor_id = Column(Integer, ForeignKey("instructors.id"), nullable=True)
    instructor = relationship("Instructor", back_populates="players")

    notes = relationship("CoachNote", back_populates="player", cascade="all, delete-orphan")
    drills = relationship("Drill", back_populates="player", cascade="all, delete-orphan")
    metrics = relationship("PlayerMetric", back_populates="player", cascade="all, delete-orphan")


class CoachNote(Base):
    __tablename__ = "coach_notes"
    id = Column(Integer, primary_key=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    instructor_id = Column(Integer, ForeignKey("instructors.id"), nullable=True)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    player = relationship("Player", back_populates="notes")


class Drill(Base):
    __tablename__ = "drills"
    id = Column(Integer, primary_key=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    instructor_id = Column(Integer, ForeignKey("instructors.id"), nullable=True)
    title = Column(String(200), nullable=False)
    file_path = Column(String(500), nullable=True)  # local path under /uploads
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    player = relationship("Player", back_populates="drills")


class Favorite(Base):
    __tablename__ = "favorites"
    id = Column(Integer, primary_key=True)
    instructor_id = Column(Integer, ForeignKey("instructors.id"), nullable=False)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)

    __table_args__ = (UniqueConstraint("instructor_id", "player_id", name="uq_fav_instructor_player"),)


class PlayerMetric(Base):
    __tablename__ = "player_metrics"
    id = Column(Integer, primary_key=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    metric_date = Column(DateTime, default=datetime.utcnow, nullable=False)
    value = Column(Integer, nullable=False, default=0)

    player = relationship("Player", back_populates="metrics")

# Create tables
Base.metadata.create_all(bind=engine)

# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

TEMPLATE_INSTRUCTOR_CANDIDATES = [
    "instructor.html",           # preferred list/clients page
    "dashboard.html",            # your existing instructor list page
    "instructor_players.html",   # older name for the list
]

TEMPLATE_PLAYER_DETAIL_CANDIDATES = [
    "instructor_player_detail.html",  # your detail template
    "player_dashboard.html",
    "player.html",
]

def render_first_existing(name_list: List[str], context: dict):
    """
    Try templates in order; always provide session to Jinja.
    """
    # Inject session so templates can do: {% if session.get('role') %}
    if "request" in context and "session" not in context:
        try:
            context["session"] = context["request"].session
        except Exception:
            context["session"] = {}

    last_exc = None
    for name in name_list:
        try:
            return templates.TemplateResponse(name, context)
        except TemplateNotFound as exc:
            last_exc = exc
            continue
    missing = ", ".join(name_list)
    raise HTTPException(status_code=500, detail=f"No template found. Looked for: {missing}. Last error: {last_exc}")

def ensure_instructor_in_session(request: Request, db: Session) -> int:
    """
    Guarantees there's an instructor in session; sets a default role for templates.
    """
    request.session.setdefault("role", "instructor")
    iid = request.session.get("instructor_id")
    if iid:
        return iid
    # fallback/default coach for first-time sessions
    inst = db.scalar(select(Instructor).where(Instructor.login_code == "DEFAULT"))
    if not inst:
        inst = Instructor(name="Coach", login_code="DEFAULT")
        db.add(inst)
        db.commit()
        db.refresh(inst)
    request.session["instructor_id"] = inst.id
    return inst.id

def current_instructor(db: Session, instructor_id: Optional[int]) -> Optional[Instructor]:
    if not instructor_id:
        return None
    return db.get(Instructor, instructor_id)

def file_save(upload: UploadFile, subdir: str) -> Optional[str]:
    """
    Save an UploadFile to /uploads/<subdir>/timestamp_filename and return relative path (for href/src).
    """
    if not upload or upload.filename is None or upload.filename == "":
        return None
    safe_name = upload.filename.replace("/", "_").replace("\\", "_")
    target_dir = UPLOADS_DIR / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    path = target_dir / f"{stamp}_{safe_name}"
    with path.open("wb") as f:
        f.write(upload.file.read())
    rel_path = f"/uploads/{subdir}/{path.name}"
    return rel_path

# Expose the uploads directory (read-only)
app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR), html=False), name="uploads")

# ------------------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    # Send users to the instructor list by default
    return RedirectResponse(url="/instructor", status_code=HTTP_302_FOUND)

# -------------------- Login (forms embedded in pages) -------------------------

@app.post("/login/instructor")
def login_instructor(request: Request, code: str = Form(...), db: Session = Depends(get_db)):
    code = (code or "").strip()
    inst = db.scalar(select(Instructor).where(Instructor.login_code == code))
    if not inst:
        # auto-provision simple instructor if new code is used
        inst = Instructor(name=f"Coach {code}", login_code=code)
        db.add(inst)
        db.commit()
        db.refresh(inst)
    request.session["instructor_id"] = inst.id
    request.session["role"] = "instructor"
    request.session["flash"] = "Logged in as instructor."
    return RedirectResponse("/instructor", status_code=HTTP_302_FOUND)

@app.post("/login/player")
def login_player(request: Request, code: str = Form(...), db: Session = Depends(get_db)):
    code = (code or "").strip()
    player = db.scalar(select(Player).where(Player.login_code == code))
    if not player:
        request.session["flash"] = "Invalid player code."
        return RedirectResponse("/instructor", status_code=HTTP_302_FOUND)
    request.session["player_id"] = player.id
    request.session["role"] = "player"
    request.session["flash"] = f"Welcome, {player.name}!"
    return RedirectResponse(f"/player/{player.id}", status_code=HTTP_302_FOUND)

# -------------------- Instructor: Clients List (was "Roster") -----------------

@app.get("/instructor", response_class=HTMLResponse)
def instructor_view(request: Request, db: Session = Depends(get_db)):
    instructor_id = ensure_instructor_in_session(request, db)

    # Load all players (avoid selecting non-existent columns)
    players_rows = db.execute(select(Player.id, Player.name, Player.photo_url, Player.phone, Player.instructor_id)).all()
    players = []
    for pid, name, photo_url, phone, instr_id in players_rows:
        players.append({
            "id": pid,
            "name": name,
            "photo_url": photo_url,
            "phone": phone,
            "instructor_id": instr_id,
        })

    # Which players are starred by this instructor?
    fav_ids = set(pid for (pid,) in db.execute(
        select(Favorite.player_id).where(Favorite.instructor_id == instructor_id)
    ).all())

    # Mark starred
    for p in players:
        p["starred"] = p["id"] in fav_ids

    # "My Clients" = starred only
    my_clients = [p for p in players if p["starred"]]

    # group by first letter for UI (optional)
    grouped = {}
    for p in players:
        k = (p["name"][:1] or "#").upper()
        grouped.setdefault(k, []).append(p)

    ctx = {
        "request": request,
        "session": request.session,
        "players": players,
        "clients": players,      # synonym if your template uses "clients"
        "roster": players,       # backward-compat if template still says "roster"
        "my_clients": my_clients,
        "grouped": grouped,
        "flash": request.session.pop("flash", None),
    }
    return render_first_existing(TEMPLATE_INSTRUCTOR_CANDIDATES, ctx)

# -------------------- Player Detail (Instructor or Player sees) ---------------

@app.get("/player/{player_id}", response_class=HTMLResponse)
def player_dashboard(player_id: int, request: Request, db: Session = Depends(get_db)):
    player = db.get(Player, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    # chart data (limit to latest 12 points)
    metrics = db.execute(
        select(PlayerMetric.metric_date, PlayerMetric.value)
        .where(PlayerMetric.player_id == player_id)
        .order_by(PlayerMetric.metric_date.asc())
        .limit(12)
    ).all()

    labels = [m.metric_date.strftime("%m/%d") for m in metrics]
    values = [m.value for m in metrics]

    notes = db.execute(
        select(CoachNote.text, CoachNote.created_at)
        .where(CoachNote.player_id == player_id)
        .order_by(CoachNote.created_at.desc())
        .limit(50)
    ).all()
    coach_notes = [{"text": t, "created_at": c} for t, c in notes]

    drills = db.execute(
        select(Drill.title, Drill.file_path, Drill.created_at)
        .where(Drill.player_id == player_id)
        .order_by(Drill.created_at.desc())
        .limit(50)
    ).all()
    drills_list = [{"title": t, "file_path": f, "created_at": c} for t, f, c in drills]

    ctx = {
        "request": request,
        "session": request.session,
        "player": {
            "id": player.id,
            "name": player.name,
            "photo_url": player.photo_url,
            "phone": player.phone,
            "login_code": player.login_code,
        },
        "chart_labels": labels,
        "chart_values": values,
        "coach_notes": coach_notes,
        "drills": drills_list,
        "flash": request.session.pop("flash", None),
    }
    return render_first_existing(TEMPLATE_PLAYER_DETAIL_CANDIDATES, ctx)

# -------------------- Create Player / Upload Photo ----------------------------

@app.post("/player/create")
async def create_player(
    request: Request,
    name: str = Form(...),
    login_code: str = Form(...),
    phone: Optional[str] = Form(None),
    photo: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    instructor_id = ensure_instructor_in_session(request, db)

    photo_url = None
    if photo:
        saved = file_save(photo, "player_photos")
        if saved:
            photo_url = saved

    # create
    p = Player(
        name=name.strip(),
        login_code=login_code.strip(),
        phone=(phone or "").strip() or None,
        photo_url=photo_url,
        instructor_id=instructor_id,
    )
    db.add(p)
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Could not create player: {e}")
    request.session["flash"] = f"Player {p.name} created."
    return RedirectResponse("/instructor", status_code=HTTP_302_FOUND)

# -------------------- Star / Unstar (My Clients) ------------------------------

@app.post("/player/{player_id}/star")
def toggle_star(player_id: int, request: Request, db: Session = Depends(get_db)):
    instructor_id = ensure_instructor_in_session(request, db)

    fav = db.scalar(
        select(Favorite).where(
            Favorite.instructor_id == instructor_id,
            Favorite.player_id == player_id,
        )
    )
    starred = False
    if fav:
        # unstar
        db.delete(fav)
        db.commit()
        starred = False
    else:
        # star
        newfav = Favorite(instructor_id=instructor_id, player_id=player_id)
        db.add(newfav)
        db.commit()
        starred = True
    return JSONResponse({"ok": True, "starred": starred, "player_id": player_id})

# -------------------- Coach Notes --------------------------------------------

@app.post("/player/{player_id}/notes")
def add_coach_note(player_id: int, request: Request, text: str = Form(...), db: Session = Depends(get_db)):
    instructor_id = ensure_instructor_in_session(request, db)
    player = db.get(Player, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    note = CoachNote(player_id=player_id, instructor_id=instructor_id, text=text.strip())
    db.add(note)
    db.commit()
    request.session["flash"] = "Note added."
    return RedirectResponse(url=f"/player/{player_id}", status_code=HTTP_302_FOUND)

# -------------------- Upload Drill -------------------------------------------

@app.post("/player/{player_id}/drills")
async def upload_drill(
    player_id: int,
    request: Request,
    title: str = Form(...),
    file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    instructor_id = ensure_instructor_in_session(request, db)
    player = db.get(Player, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    file_path = None
    if file:
        saved = file_save(file, "drills")
        if saved:
            file_path = saved

    drill = Drill(player_id=player_id, instructor_id=instructor_id, title=title.strip(), file_path=file_path)
    db.add(drill)
    db.commit()
    request.session["flash"] = "Drill uploaded."
    return RedirectResponse(url=f"/player/{player_id}", status_code=HTTP_302_FOUND)

# -------------------- Text Player (Twilio optional) --------------------------

def _twilio_send(to_phone: str, body: str) -> bool:
    if not (TWILIO_SID and TWILIO_TOKEN and TWILIO_FROM):
        return False
    try:
        from twilio.rest import Client
        client = Client(TWILIO_SID, TWILIO_TOKEN)
        client.messages.create(to=to_phone, from_=TWILIO_FROM, body=body)
        return True
    except Exception:
        return False

@app.post("/player/{player_id}/text")
def text_player(player_id: int, request: Request, message: str = Form(...), db: Session = Depends(get_db)):
    player = db.get(Player, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    ok = False
    if player.phone:
        ok = _twilio_send(player.phone, message.strip())

    if ok:
        request.session["flash"] = f"Text sent to {player.name}."
    else:
        # Soft-fail: no creds or phone; still show a friendly flash
        request.session["flash"] = "Text service not configured or player has no phone number."

    return RedirectResponse(url=f"/player/{player_id}", status_code=HTTP_302_FOUND)

# -------------------- Add quick metric point (for demo/testing) --------------

@app.post("/player/{player_id}/metric")
def add_metric(player_id: int, request: Request, value: int = Form(...), db: Session = Depends(get_db)):
    player = db.get(Player, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    m = PlayerMetric(player_id=player_id, value=int(value))
    db.add(m)
    db.commit()
    request.session["flash"] = "Metric added."
    return RedirectResponse(url=f"/player/{player_id}", status_code=HTTP_302_FOUND)

# -------------------- Health / Root helpers ----------------------------------

@app.head("/", response_class=PlainTextResponse)
def head_root():
    return PlainTextResponse("OK", status_code=200)

@app.get("/healthz", response_class=PlainTextResponse)
def healthz():
    return PlainTextResponse("ok")
