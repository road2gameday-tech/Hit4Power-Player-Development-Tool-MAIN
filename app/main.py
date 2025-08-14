import os
from fastapi import FastAPI, Request, Depends, Form, UploadFile, File, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func, select, delete
from typing import Optional, List
from datetime import datetime
from itsdangerous import URLSafeSerializer
from jinja2 import pass_environment
from markupsafe import Markup, escape

from .database import SessionLocal, engine, Base
from .models import Player, Instructor, Metric, Note, Drill, DrillAssignment, InstructorFavorite
from .utils import generate_code, age_bucket

# ----- App setup -----
app = FastAPI(title="Hit4Power Development Tool")
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SECRET_KEY", "dev-secret"))

# static & templates
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

# Jinja filters
@pass_environment
def datetimeformat(env, value, fmt="%b %d, %Y"):
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.strftime(fmt)
    try:
        return datetime.fromisoformat(str(value)).strftime(fmt)
    except Exception:
        return str(value)
templates.env.filters["datetimeformat"] = datetimeformat
templates.env.filters["age_bucket"] = age_bucket

# ----- DB dependency -----
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ----- Initialize DB -----
Base.metadata.create_all(bind=engine)

# Seed a default instructor if none exists
def ensure_default_instructor(db: Session):
    default_code = os.getenv("INSTRUCTOR_DEFAULT_CODE", "change-me-1234")
    existing = db.execute(select(Instructor).limit(1)).scalar_one_or_none()
    if not existing:
        coach = Instructor(name="Coach", login_code=default_code)
        db.add(coach); db.commit()

# ----- Helpers -----
def current_player(request: Request, db: Session) -> Optional[Player]:
    pid = request.session.get("player_id")
    if not pid:
        return None
    return db.get(Player, pid)

def current_instructor(request: Request, db: Session) -> Optional[Instructor]:
    iid = request.session.get("instructor_id")
    if not iid:
        return None
    return db.get(Instructor, iid)

def twilio_client():
    from twilio.rest import Client
    sid = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    if not sid or not token:
        return None
    return Client(sid, token)

def send_text_async(to_number: str, body: str):
    client = twilio_client()
    if not client or not to_number:
        return
    from_num = os.getenv("TWILIO_FROM_NUMBER")
    try:
        client.messages.create(to=to_number, from_=from_num, body=body)
    except Exception as e:
        # swallow errors in background
        print("Twilio error:", e)

# ----- Routes -----

@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    ensure_default_instructor(db)
    ctx = {"request": request, "title": "Login"}
    return templates.TemplateResponse("index.html", ctx)

@app.post("/login/player")
def login_player(request: Request, code: str = Form(...), db: Session = Depends(get_db)):
    p = db.execute(select(Player).where(Player.login_code == code.strip())).scalar_one_or_none()
    if not p:
        return templates.TemplateResponse("index.html", {"request": request, "error": "Invalid player code"})
    request.session.clear()
    request.session["player_id"] = p.id
    return RedirectResponse(url="/dashboard", status_code=303)

@app.post("/login/instructor")
def login_instructor(request: Request, code: str = Form(...), db: Session = Depends(get_db)):
    i = db.execute(select(Instructor).where(Instructor.login_code == code.strip())).scalar_one_or_none()
    if not i:
        return templates.TemplateResponse("index.html", {"request": request, "error": "Invalid instructor code"})
    request.session.clear()
    request.session["instructor_id"] = i.id
    return RedirectResponse(url="/instructor", status_code=303)

@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)

# ----- Player dashboard -----
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    player = current_player(request, db)
    if not player:
        return RedirectResponse("/", status_code=303)
    # metrics sorted by date
    rows = db.execute(select(Metric).where(Metric.player_id == player.id).order_by(Metric.date.asc())).scalars().all()
    dates = [m.date.strftime("%Y-%m-%d") for m in rows]
    exitv = [m.exit_velocity or 0 for m in rows]
    shared_notes = db.execute(select(Note).where(Note.player_id == player.id, Note.shared == True).order_by(Note.created_at.desc())).scalars().all()
    drills = db.execute(select(DrillAssignment).where(DrillAssignment.player_id == player.id).order_by(DrillAssignment.created_at.desc())).scalars().all()
    ctx = {
        "request": request,
        "player": player,
        "dates": dates,
        "exitv": exitv,
        "shared_notes": shared_notes,
        "drills": drills,
    }
    return templates.TemplateResponse("dashboard.html", ctx)

# ----- Instructor dashboard (Clients) -----
@app.get("/instructor", response_class=HTMLResponse)
def instructor_home(request: Request, db: Session = Depends(get_db)):
    instr = current_instructor(request, db)
    if not instr:
        return RedirectResponse("/", status_code=303)

    # group by age buckets
    players = db.execute(select(Player)).scalars().all()
    # sessions count per player (metric entries)
    counts = dict(db.execute(select(Metric.player_id, func.count(Metric.id)).group_by(Metric.player_id)).all())
    # favorites
    fav_ids = set([fav.player_id for fav in instr.favorites])

    grouped = {"7-9": [], "10-12": [], "13-15": [], "16-18": [], "18+": []}
    for p in players:
        bucket = age_bucket(p.age)
        if bucket in grouped:
            grouped[bucket].append((p, counts.get(p.id, 0), (p.id in fav_ids)))

    my_clients = [ (p, counts.get(p.id,0)) for (p, c, fav) in [(pp, counts.get(pp.id,0), (pp.id in fav_ids)) for pp in players] if (p.id in fav_ids) ]

    ctx = {
        "request": request,
        "instr": instr,
        "grouped": grouped,
        "fav_ids": fav_ids,
        "my_clients_count": len(fav_ids),
    }
    return templates.TemplateResponse("instructor_dashboard.html", ctx)

# Toggle favorite
@app.post("/favorite/{player_id}")
def favorite_player(request: Request, player_id: int, db: Session = Depends(get_db)):
    instr = current_instructor(request, db)
    if not instr:
        raise HTTPException(status_code=403, detail="Not instructor")
    existing = db.execute(select(InstructorFavorite).where(
        InstructorFavorite.instructor_id == instr.id, InstructorFavorite.player_id == player_id
    )).scalar_one_or_none()
    if existing:
        db.execute(delete(InstructorFavorite).where(InstructorFavorite.id == existing.id))
        db.commit()
        return JSONResponse({"favorited": False})
    fav = InstructorFavorite(instructor_id=instr.id, player_id=player_id)
    db.add(fav); db.commit()
    return JSONResponse({"favorited": True})

# Create player
@app.post("/players/create", response_class=HTMLResponse)
async def create_player(
    request: Request,
    name: str = Form(...),
    age: int = Form(...),
    phone: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    instr = current_instructor(request, db)
    if not instr:
        return RedirectResponse("/", status_code=303)
    code = generate_code(prefix="P-")
    p = Player(name=name.strip(), age=int(age), login_code=code, phone=phone.strip() if phone else None)
    db.add(p); db.commit(); db.refresh(p)

    # save image if provided
    if image and image.filename:
        uploads_dir = "app/static/uploads/players"
        os.makedirs(uploads_dir, exist_ok=True)
        ext = os.path.splitext(image.filename)[1].lower() or ".jpg"
        path = f"{uploads_dir}/player_{p.id}{ext}"
        with open(path, "wb") as f:
            f.write(await image.read())
        p.image_path = path.replace("app/", "/")  # serve under /static
        db.add(p); db.commit()

    # Show success on instructor page with code
    request.session["flash"] = f"Player created. Login code: {code}"
    return RedirectResponse("/instructor", status_code=303)

# Bulk CSV upload (name,age,phone)
@app.post("/players/bulk_upload")
async def bulk_upload(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    instr = current_instructor(request, db)
    if not instr:
        return RedirectResponse("/", status_code=303)
    content = (await file.read()).decode("utf-8", errors="ignore")
    created = []
    for line in content.splitlines():
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(",")]
        if not parts:
            continue
        name = parts[0]
        try:
            age = int(parts[1]) if len(parts) > 1 and parts[1] else 12
        except:
            age = 12
        phone = parts[2] if len(parts) > 2 else None
        code = generate_code(prefix="P-")
        p = Player(name=name, age=age, login_code=code, phone=phone)
        db.add(p); db.flush()
        created.append((name, code))
    db.commit()
    request.session["flash"] = f"Imported {len(created)} players."
    return RedirectResponse("/instructor", status_code=303)

# Instructor player detail
@app.get("/instructor/player/{player_id}", response_class=HTMLResponse)
def instructor_player_detail(request: Request, player_id: int, db: Session = Depends(get_db)):
    instr = current_instructor(request, db)
    if not instr:
        return RedirectResponse("/", status_code=303)
    p = db.get(Player, player_id)
    if not p:
        raise HTTPException(status_code=404, detail="Player not found")
    metrics = db.execute(select(Metric).where(Metric.player_id == p.id).order_by(Metric.date.desc())).scalars().all()
    notes = db.execute(select(Note).where(Note.player_id == p.id).order_by(Note.created_at.desc())).scalars().all()
    drills = db.execute(select(Drill).order_by(Drill.created_at.desc())).scalars().all()
    ctx = {"request": request, "player": p, "metrics": metrics, "notes": notes, "drills": drills}
    return templates.TemplateResponse("instructor_player_detail.html", ctx)

# Add metric (instructor only)
@app.post("/metrics/add")
def add_metric(
    request: Request,
    background_tasks: BackgroundTasks,
    player_id: int = Form(...),
    date: Optional[str] = Form(None),
    exit_velocity: Optional[float] = Form(None),
    launch_angle: Optional[float] = Form(None),
    spin_rate: Optional[float] = Form(None),
    db: Session = Depends(get_db),
):
    instr = current_instructor(request, db)
    if not instr:
        raise HTTPException(status_code=403, detail="Only instructors can add metrics")
    p = db.get(Player, player_id)
    if not p:
        raise HTTPException(status_code=404, detail="Player not found")
    dt = datetime.fromisoformat(date) if date else datetime.utcnow()
    m = Metric(player_id=p.id, date=dt, exit_velocity=exit_velocity, launch_angle=launch_angle, spin_rate=spin_rate)
    db.add(m); db.commit()

    # Optional SMS notify
    if p.phone and os.getenv("TWILIO_ACCOUNT_SID") and os.getenv("TWILIO_FROM_NUMBER"):
        base = os.getenv("BASE_URL", "http://localhost:8000")
        body = f"Hit4Power update: New metrics posted for {p.name}. View: {base}"
        background_tasks.add_task(send_text_async, p.phone, body)

    return RedirectResponse(f"/instructor/player/{p.id}", status_code=303)

# Add note (instructor only)
@app.post("/notes/add")
def add_note(
    request: Request,
    background_tasks: BackgroundTasks,
    player_id: int = Form(...),
    text: str = Form(...),
    share_with_player: Optional[bool] = Form(False),
    text_player: Optional[bool] = Form(False),
    db: Session = Depends(get_db),
):
    instr = current_instructor(request, db)
    if not instr:
        raise HTTPException(status_code=403, detail="Only instructors can add notes")
    p = db.get(Player, player_id)
    if not p:
        raise HTTPException(status_code=404, detail="Player not found")
    n = Note(player_id=p.id, instructor_id=instr.id, text=text.strip(), shared=bool(share_with_player))
    db.add(n); db.commit()

    # Optionally text the player
    if text_player and p.phone and os.getenv("TWILIO_ACCOUNT_SID") and os.getenv("TWILIO_FROM_NUMBER"):
        background_tasks.add_task(send_text_async, p.phone, f"Coach note from {instr.name}: {text.strip()}")

    return RedirectResponse(f"/instructor/player/{p.id}", status_code=303)

# Assign drill
@app.post("/drills/assign")
def assign_drill(
    request: Request,
    player_id: int = Form(...),
    drill_id: int = Form(...),
    note: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    instr = current_instructor(request, db)
    if not instr:
        raise HTTPException(status_code=403, detail="Only instructors can assign drills")
    da = DrillAssignment(player_id=player_id, instructor_id=instr.id, drill_id=drill_id, note=note or "")
    db.add(da); db.commit()
    return RedirectResponse(f"/instructor/player/{player_id}", status_code=303)

# Manage drills (simple add)
@app.post("/drills/create")
def create_drill(
    request: Request,
    title: str = Form(...),
    description: Optional[str] = Form(None),
    video_url: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    instr = current_instructor(request, db)
    if not instr:
        raise HTTPException(status_code=403, detail="Only instructors")
    d = Drill(title=title.strip(), description=(description or "").strip(), video_url=(video_url or "").strip())
    db.add(d); db.commit()
    return RedirectResponse("/instructor", status_code=303)

# ----- Templates: flash helper -----
def pop_flash(request: Request) -> Optional[str]:
    msg = request.session.get("flash")
    if msg:
        request.session.pop("flash")
        return msg
    return None
templates.env.globals["pop_flash"] = pop_flash

