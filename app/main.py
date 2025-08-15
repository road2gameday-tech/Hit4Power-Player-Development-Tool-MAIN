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
    create_engine, Column, Integer, String, DateTime, ForeignKey, Text, select, UniqueConstraint
)
from sqlalchemy.orm import sessionmaker, declarative_base, Session, relationship
from sqlalchemy.exc import OperationalError

# ------------------------------------------------------------------------------
# Paths & config
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
# App
# ------------------------------------------------------------------------------
app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# ------------------------------------------------------------------------------
# DB
# ------------------------------------------------------------------------------
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

class Instructor(Base):
    __tablename__ = "instructors"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    login_code = Column(String(64), unique=True, nullable=False)

class Player(Base):
    __tablename__ = "players"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    login_code = Column(String(64), unique=True, nullable=False)
    # Keep these if they already exist in your DB; we handle missing columns gracefully.
    phone = Column(String(32), nullable=True)
    photo_url = Column(String(500), nullable=True)

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
    file_path = Column(String(500), nullable=True)
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

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ------------------------------------------------------------------------------
# Rendering helper — ALWAYS inject `session`
# ------------------------------------------------------------------------------
def render(name: str, request: Request, **ctx):
    ctx.setdefault("request", request)
    ctx.setdefault("session", request.session)
    return templates.TemplateResponse(name, ctx)

# ------------------------------------------------------------------------------
# Session helpers
# ------------------------------------------------------------------------------
def ensure_instructor_in_session(request: Request, db: Session) -> int:
    request.session.setdefault("role", "instructor")
    iid = request.session.get("instructor_id")
    if iid:
        return iid
    # Bootstrap a default instructor so the UI works without manual seeding.
    inst = db.query(Instructor).filter_by(login_code="DEFAULT").first()
    if not inst:
        inst = Instructor(name="Coach", login_code="DEFAULT")
        db.add(inst); db.commit(); db.refresh(inst)
    request.session["instructor_id"] = inst.id
    return inst.id

# ------------------------------------------------------------------------------
# Root & health
# ------------------------------------------------------------------------------
@app.head("/", response_class=PlainTextResponse)
def head_root():
    return PlainTextResponse("OK", status_code=200)

@app.get("/", response_class=HTMLResponse)
def root():
    return RedirectResponse("/instructor", status_code=HTTP_302_FOUND)

@app.get("/healthz", response_class=PlainTextResponse)
def healthz():
    return PlainTextResponse("ok")

# ------------------------------------------------------------------------------
# Logins (forms are embedded in your pages)
# ------------------------------------------------------------------------------
@app.post("/login/instructor")
def login_instructor(request: Request, code: str = Form(...), db: Session = Depends(get_db)):
    code = (code or "").strip()
    inst = db.query(Instructor).filter_by(login_code=code).first()
    if not inst:
        inst = Instructor(name=f"Coach {code}", login_code=code)
        db.add(inst); db.commit(); db.refresh(inst)
    request.session["instructor_id"] = inst.id
    request.session["role"] = "instructor"
    request.session["flash"] = "Logged in as instructor."
    return RedirectResponse("/instructor", status_code=HTTP_302_FOUND)

@app.post("/login/player")
def login_player(request: Request, code: str = Form(...), db: Session = Depends(get_db)):
    code = (code or "").strip()
    player = db.query(Player).filter_by(login_code=code).first()
    if not player:
        request.session["flash"] = "Invalid player code."
        return RedirectResponse("/instructor", status_code=HTTP_302_FOUND)
    request.session["player_id"] = player.id
    request.session["role"] = "player"
    request.session["flash"] = f"Welcome, {player.name}!"
    return RedirectResponse(f"/player/{player.id}", status_code=HTTP_302_FOUND)

# ------------------------------------------------------------------------------
# Instructor dashboard (Clients)
# ------------------------------------------------------------------------------
@app.get("/instructor", response_class=HTMLResponse)
def instructor_view(request: Request, db: Session = Depends(get_db)):
    instructor_id = ensure_instructor_in_session(request, db)

    # Be defensive about older DBs that may miss optional columns like phone/photo_url.
    players: List[dict] = []
    tried = [
        (Player.id, Player.name, Player.photo_url, Player.phone),
        (Player.id, Player.name, Player.photo_url),
        (Player.id, Player.name),
    ]
    last_err: Optional[Exception] = None
    for sel in tried:
        try:
            rows = db.execute(select(*sel)).all()
            for row in rows:
                data = {"id": row[0], "name": row[1]}
                # Safely attach optional fields:
                if len(row) >= 3:
                    data["photo_url"] = row[2]
                else:
                    data["photo_url"] = None
                if len(row) >= 4:
                    data["phone"] = row[3]
                else:
                    data["phone"] = None
                players.append(data)
            break
        except OperationalError as e:
            last_err = e
            players = []
            continue
    if not players and last_err:
        # Surface a friendly message but keep the page alive.
        request.session["flash"] = "Your player table is from an older version. Page loaded without some fields."

    fav_ids = {pid for (pid,) in db.execute(
        select(Favorite.player_id).where(Favorite.instructor_id == instructor_id)
    ).all()}
    for p in players:
        p["starred"] = p["id"] in fav_ids

    ctx = {
        "players": players,
        "clients": players,
        "my_clients": [p for p in players if p["starred"]],
        "flash": request.session.pop("flash", None),
    }
    return render("dashboard.html", request, **ctx)

# ------------------------------------------------------------------------------
# Player detail (instructor & player)
# ------------------------------------------------------------------------------
@app.get("/player/{player_id}", response_class=HTMLResponse)
def player_dashboard(player_id: int, request: Request, db: Session = Depends(get_db)):
    player = db.get(Player, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    metrics = db.execute(
        select(PlayerMetric.metric_date, PlayerMetric.value)
        .where(PlayerMetric.player_id == player_id)
        .order_by(PlayerMetric.metric_date.asc())
        .limit(12)
    ).all()
    labels = [d.strftime("%m/%d") for d, _ in metrics]
    values = [v for _, v in metrics]

    notes = db.execute(
        select(CoachNote.text, CoachNote.created_at)
        .where(CoachNote.player_id == player_id)
        .order_by(CoachNote.created_at.desc())
        .limit(50)
    ).all()
    drills = db.execute(
        select(Drill.title, Drill.file_path, Drill.created_at)
        .where(Drill.player_id == player_id)
        .order_by(Drill.created_at.desc())
        .limit(50)
    ).all()

    ctx = {
        "player": {
            "id": player.id,
            "name": player.name,
            "photo_url": getattr(player, "photo_url", None),
            "phone": getattr(player, "phone", None),
            "login_code": player.login_code,
        },
        "chart_labels": labels,
        "chart_values": values,
        "coach_notes": [{"text": t, "created_at": c} for (t, c) in notes],
        "drills": [{"title": t, "file_path": f, "created_at": c} for (t, f, c) in drills],
        "flash": request.session.pop("flash", None),
    }
    # Your template name:
    return render("instructor_player_detail.html", request, **ctx)

# ------------------------------------------------------------------------------
# Create player
# ------------------------------------------------------------------------------
@app.post("/player/create")
async def create_player(
    request: Request,
    name: str = Form(...),
    login_code: str = Form(...),
    phone: Optional[str] = Form(None),
    photo: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    # No instructor_id stored on players — starring uses the favorites table.
    photo_url = None
    if photo and photo.filename:
        photo_url = save_file(photo, "player_photos")

    p = Player(
        name=name.strip(),
        login_code=login_code.strip(),
        phone=(phone or "").strip() or None,
        photo_url=photo_url,
    )
    db.add(p)
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Could not create player: {e}")
    request.session["flash"] = f"Player {p.name} created."
    return RedirectResponse("/instructor", status_code=HTTP_302_FOUND)

# ------------------------------------------------------------------------------
# Star / Unstar (My Clients)
# ------------------------------------------------------------------------------
@app.post("/player/{player_id}/star")
def toggle_star(player_id: int, request: Request, db: Session = Depends(get_db)):
    instructor_id = ensure_instructor_in_session(request, db)
    fav = db.query(Favorite).filter_by(instructor_id=instructor_id, player_id=player_id).first()
    if fav:
        db.delete(fav); db.commit()
        return JSONResponse({"ok": True, "starred": False, "player_id": player_id})
    db.add(Favorite(instructor_id=instructor_id, player_id=player_id)); db.commit()
    return JSONResponse({"ok": True, "starred": True, "player_id": player_id})

# ------------------------------------------------------------------------------
# Coach notes
# ------------------------------------------------------------------------------
@app.post("/player/{player_id}/notes")
def add_note(player_id: int, request: Request, text: str = Form(...), db: Session = Depends(get_db)):
    player = db.get(Player, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    db.add(CoachNote(player_id=player_id, text=text.strip()))
    db.commit()
    request.session["flash"] = "Note added."
    return RedirectResponse(f"/player/{player_id}", status_code=HTTP_302_FOUND)

# ------------------------------------------------------------------------------
# Upload drill
# ------------------------------------------------------------------------------
@app.post("/player/{player_id}/drills")
async def upload_drill(
    player_id: int,
    request: Request,
    title: str = Form(...),
    file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    player = db.get(Player, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    file_path = save_file(file, "drills") if (file and file.filename) else None
    db.add(Drill(player_id=player_id, title=title.strip(), file_path=file_path))
    db.commit()
    request.session["flash"] = "Drill uploaded."
    return RedirectResponse(f"/player/{player_id}", status_code=HTTP_302_FOUND)

# ------------------------------------------------------------------------------
# Text player (Twilio optional)
# ------------------------------------------------------------------------------
def _twilio_send(to_phone: str, body: str) -> bool:
    if not (TWILIO_SID and TWILIO_TOKEN and TWILIO_FROM):
        return False
    try:
        from twilio.rest import Client
        Client(TWILIO_SID, TWILIO_TOKEN).messages.create(to=to_phone, from_=TWILIO_FROM, body=body)
        return True
    except Exception:
        return False

@app.post("/player/{player_id}/text")
def text_player(player_id: int, request: Request, message: str = Form(...), db: Session = Depends(get_db)):
    player = db.get(Player, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    ok = bool(getattr(player, "phone", None)) and _twilio_send(player.phone, message.strip())
    request.session["flash"] = f"Text sent to {player.name}." if ok else "Text service not configured or no phone."
    return RedirectResponse(f"/player/{player_id}", status_code=HTTP_302_FOUND)

# ------------------------------------------------------------------------------
# Quick metric (demo)
# ------------------------------------------------------------------------------
@app.post("/player/{player_id}/metric")
def add_metric(player_id: int, request: Request, value: int = Form(...), db: Session = Depends(get_db)):
    player = db.get(Player, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    db.add(PlayerMetric(player_id=player_id, value=int(value))); db.commit()
    request.session["flash"] = "Metric added."
    return RedirectResponse(f"/player/{player_id}", status_code=HTTP_302_FOUND)

# ------------------------------------------------------------------------------
# Utils
# ------------------------------------------------------------------------------
def save_file(upload: UploadFile, subdir: str) -> Optional[str]:
    if not upload:
        return None
    safe = upload.filename.replace("/", "_").replace("\\", "_")
    folder = UPLOADS_DIR / subdir
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    path = folder / f"{stamp}_{safe}"
    with path.open("wb") as f:
        f.write(upload.file.read())
    return f"/uploads/{subdir}/{path.name}"
