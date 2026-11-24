import hashlib
import hmac
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

import jwt
import requests
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from models import Base, LobbyRequest, Meeting, OrgSettings, User
from livekit_admin import list_participants, list_rooms, mute_participant, remove_participant
from security import (
    create_session_token,
    decode_session_token,
    hash_password,
    livekit_credentials,
    verify_password,
)

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:////data/app.db")
CONTROL_PLANE_URL = os.environ.get("CONTROL_PLANE_URL", "")
DEVICE_ID = os.environ.get("DEVICE_ID", "")
ROOT_DOMAIN = os.environ.get("ROOT_DOMAIN", "domainutama.com")
LIVEKIT_URL = os.environ.get("LIVEKIT_URL", "ws://127.0.0.1:7880")
MAX_USERS = int(os.environ.get("MAX_USERS", "20"))
MAX_CONCURRENT_MEETINGS = int(os.environ.get("MAX_CONCURRENT_MEETINGS", "5"))

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)

app = FastAPI(title="Appliance Dashboard")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.on_event("startup")
def startup():
    Base.metadata.create_all(engine)
    migrate()


def migrate():
    """Add columns introduced after a device was first provisioned (create_all only adds tables)."""
    cols = {c["name"] for c in inspect(engine).get_columns("meetings")}
    if "show_on_screen" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE meetings ADD COLUMN show_on_screen BOOLEAN DEFAULT 1"))


def require_admin(user):
    if user.role != "admin":
        raise HTTPException(403, "admin only")


def require_host_or_admin(user, m):
    if user.role != "admin" and m.host_user_id != user.id:
        raise HTTPException(403, "host only")


def current_user(authorization: str = Header(None), db=Depends(get_db)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing token")
    try:
        payload = decode_session_token(authorization[7:])
    except Exception:
        raise HTTPException(401, "invalid token")
    user = db.query(User).filter_by(id=int(payload["sub"])).first()
    if not user or not user.active:
        raise HTTPException(401, "unknown user")
    return user


SITE_FILE = os.environ.get("SITE_FILE", "/etc/appliance/site_fqdn")
LAN_IP_FILE = os.environ.get("LAN_IP_FILE", "/etc/appliance/lan_ip")
# Internet access: the public name, written by the agent while the device's tunnel is connected.
PUBLIC_FILE = os.environ.get("PUBLIC_FILE", "/etc/appliance/public_fqdn")
ROOM_SEED_FILE = os.environ.get("ROOM_SEED_FILE", "/etc/appliance/room_seed")


def first_line(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def public_base():
    """The meeting link everyone gets: the public name while the tunnel is up (works in the room
    and from anywhere), otherwise the office-network link."""
    host = first_line(PUBLIC_FILE)
    return f"https://{host}" if host else local_base()


def room_keys(at=None):
    """Keys for the room-screen QR (the kiosk computes the same): today's and yesterday's (UTC),
    so a page opened just before midnight keeps working."""
    seed = first_line(ROOM_SEED_FILE)
    if not seed:
        return []
    day = (at or datetime.now(timezone.utc)).date()
    return [hmac.new(seed.encode(), f"now:{d.isoformat()}".encode(), hashlib.sha256).hexdigest()[:20]
            for d in (day, day - timedelta(days=1))]


def from_internet(request):
    """Set only by Caddy's tunnel listener; stripped from every other request."""
    return request.headers.get("x-vipcon-public") == "1"


def local_base():
    """Office-network link: the device's name once its certificate is in place (written by the
    agent), otherwise its LAN IP."""
    host = first_line(SITE_FILE) or first_line(LAN_IP_FILE) or "localhost"
    return f"https://{host}"


def parse_dt(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def meeting_out(m):
    return {
        "id": m.id,
        "title": m.title,
        "room_name": m.room_name,
        "lobby": m.lobby,
        "e2ee": m.e2ee,
        "locked": m.locked,
        "status": m.status,
        "scheduled_at": m.scheduled_at.isoformat() if m.scheduled_at else None,
        "duration_minutes": m.duration_minutes,
        "has_passcode": bool(m.passcode_hash),
        "max_participants": m.max_participants,
        "show_on_screen": m.show_on_screen is not False,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "url": f"{public_base()}/m/{m.room_name}",
        "local_url": f"{local_base()}/m/{m.room_name}",
    }


class ClaimRequest(BaseModel):
    claim_code: str
    username: str
    password: str
    display_name: str = ""
    org_name: str = ""
    device_id: str = ""  # ignored: the device's own id is used


USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
MIN_PASSWORD = 10
_claim_lock = threading.Lock()


class LoginRequest(BaseModel):
    username: str
    password: str


class UserCreate(BaseModel):
    username: str
    password: str
    display_name: str = ""
    role: str = "member"


class MeetingCreate(BaseModel):
    title: str
    scheduled_at: str = None
    passcode: str = None
    lobby: bool = True
    e2ee: bool = False
    max_participants: int = 10
    duration_minutes: int = 60
    show_on_screen: bool = True


class JoinRequest(BaseModel):
    room: str
    identity: str
    passcode: str = None


class HostTokenRequest(BaseModel):
    room: str
    identity: str


class ModerateRequest(BaseModel):
    identity: str


class MuteRequest(BaseModel):
    identity: str
    muted: bool = True


class SettingsRequest(BaseModel):
    org_name: str = None
    logo_path: str = None
    theme: dict = None


@app.get("/healthz")
def healthz(db=Depends(get_db)):
    # "claimed" is read by the LCD kiosk to decide whether to offer the claim QR.
    return {"ok": True, "claimed": db.query(User).first() is not None}


@app.get("/api/v1/ca.pem")
def ca_pem():
    return serve_ca_file("ca.pem", "application/x-pem-file", "vipcon-ca.pem")


CA_DIR = os.environ.get("CA_DIR", "/etc/appliance/ca")


def serve_ca_file(name, media_type, filename):
    try:
        with open(f"{CA_DIR}/{name}") as f:
            content = f.read()
    except OSError:
        raise HTTPException(404, "CA not available")
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/v1/ca.crt")
def ca_crt():
    return serve_ca_file("ca.crt", "application/x-x509-ca-cert", "vipcon-ca.crt")


@app.get("/api/v1/ca.mobileconfig")
def ca_mobileconfig():
    return serve_ca_file("ca.mobileconfig", "application/x-apple-aspen-config", "vipcon-ca.mobileconfig")


@app.get("/api/v1/install-ca.sh")
def install_ca_sh():
    return serve_ca_file("install-ca.sh", "text/x-shellscript", "install-ca.sh")


@app.get("/api/v1/install-ca.bat")
def install_ca_bat():
    return serve_ca_file("install-ca.bat", "application/x-msdownload", "install-ca.bat")


@app.get("/api/v1/claim/status")
def claim_status(db=Depends(get_db)):
    return {"claimed": db.query(User).first() is not None, "device_id": DEVICE_ID}


@app.post("/api/v1/claim")
def claim(req: ClaimRequest, db=Depends(get_db)):
    """First-run activation: verify the one-time claim code with the control plane, then
    create the Client Admin. Reached from the QR on the device's LCD."""
    username = req.username.strip().lower()
    display_name = req.display_name.strip()[:80]
    org_name = req.org_name.strip()[:100]
    if not USERNAME_RE.match(username):
        raise HTTPException(422, "Username must be 3–32 characters: lowercase letters, numbers, dots, hyphens or underscores.")
    if len(req.password) < MIN_PASSWORD:
        raise HTTPException(422, f"Password must be at least {MIN_PASSWORD} characters.")
    if req.password.lower() == username:
        raise HTTPException(422, "Password must not be the same as the username.")
    if not req.claim_code or len(req.claim_code) > 128:
        raise HTTPException(400, "Invalid activation code.")
    if not CONTROL_PLANE_URL or not DEVICE_ID:
        raise HTTPException(503, "This device has not been provisioned yet.")
    # One claim at a time; the control plane also only accepts the code once.
    with _claim_lock:
        if db.query(User).first() is not None:
            raise HTTPException(409, "This device is already activated. Please sign in.")
        try:
            r = requests.post(
                f"{CONTROL_PLANE_URL}/api/v1/claim/verify",
                json={"device_id": DEVICE_ID, "claim_code": req.claim_code},
                timeout=10,
            )
        except requests.RequestException:
            raise HTTPException(502, "Cannot reach the Vipcon server. Check the device's internet connection.")
        if r.status_code == 403:
            raise HTTPException(403, "This device is suspended. Please contact your Vipcon administrator.")
        if r.status_code == 409:
            raise HTTPException(409, "This activation code has already been used. Ask your Vipcon administrator for a new one.")
        if r.status_code != 200:
            raise HTTPException(400, "The activation code is invalid or has already been used.")
        user = User(
            username=username,
            display_name=display_name or username,
            password_hash=hash_password(req.password),
            role="admin",
            active=True,
        )
        db.add(user)
        if org_name:
            settings = db.query(OrgSettings).first() or OrgSettings()
            settings.org_name = org_name
            db.add(settings)
        db.commit()
    return {"ok": True, "token": create_session_token(user.id), "username": username}


@app.post("/api/v1/login")
def login(req: LoginRequest, db=Depends(get_db)):
    user = db.query(User).filter_by(username=req.username).first()
    if not user or not user.active:
        raise HTTPException(401, "invalid credentials")
    if user.locked_until and user.locked_until > datetime.now(timezone.utc):
        raise HTTPException(423, "locked")
    if not verify_password(req.password, user.password_hash):
        user.failed_attempts += 1
        if user.failed_attempts >= 5:
            user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=15)
            user.failed_attempts = 0
        db.commit()
        raise HTTPException(401, "invalid credentials")
    user.failed_attempts = 0
    user.last_login = datetime.now(timezone.utc)
    db.commit()
    return {"token": create_session_token(user.id), "user": {"id": user.id, "username": user.username, "role": user.role}}


@app.get("/api/v1/me")
def me(user=Depends(current_user)):
    return {"id": user.id, "username": user.username, "display_name": user.display_name, "role": user.role}


@app.get("/api/v1/users")
def list_users(user=Depends(current_user), db=Depends(get_db)):
    require_admin(user)
    return [
        {"id": u.id, "username": u.username, "display_name": u.display_name, "role": u.role, "active": u.active}
        for u in db.query(User).all()
    ]


@app.post("/api/v1/users")
def create_user(req: UserCreate, user=Depends(current_user), db=Depends(get_db)):
    require_admin(user)
    if db.query(User).filter_by(username=req.username).first():
        raise HTTPException(409, "username exists")
    if db.query(User).count() >= MAX_USERS:
        raise HTTPException(429, f"user limit reached ({MAX_USERS})")
    u = User(username=req.username, display_name=req.display_name, password_hash=hash_password(req.password), role=req.role, active=True)
    db.add(u)
    db.commit()
    return {"id": u.id}


@app.delete("/api/v1/users/{user_id}")
def delete_user(user_id: int, user=Depends(current_user), db=Depends(get_db)):
    require_admin(user)
    if user_id == user.id:
        raise HTTPException(400, "cannot delete self")
    u = db.query(User).filter_by(id=user_id).first()
    if not u:
        raise HTTPException(404, "not found")
    db.delete(u)
    db.commit()
    return {"ok": True}


@app.post("/api/v1/meetings")
def create_meeting(req: MeetingCreate, user=Depends(current_user), db=Depends(get_db)):
    if user.role not in ("admin", "host"):
        raise HTTPException(403, "host only")
    active = db.query(Meeting).filter(Meeting.status != "ended").count()
    if active >= MAX_CONCURRENT_MEETINGS:
        raise HTTPException(429, f"meeting limit reached ({MAX_CONCURRENT_MEETINGS})")
    m = Meeting(
        title=req.title,
        room_name=secrets.token_urlsafe(12),
        passcode_hash=hash_password(req.passcode) if req.passcode else None,
        host_user_id=user.id,
        scheduled_at=parse_dt(req.scheduled_at),
        duration_minutes=req.duration_minutes,
        lobby=req.lobby,
        e2ee=req.e2ee,
        max_participants=req.max_participants,
        show_on_screen=req.show_on_screen,
        status="scheduled",
    )
    db.add(m)
    db.commit()
    out = meeting_out(m)
    if req.passcode:
        out["passcode"] = req.passcode
    return out


@app.get("/api/v1/meetings")
def list_meetings(user=Depends(current_user), db=Depends(get_db)):
    return [meeting_out(m) for m in db.query(Meeting).order_by(Meeting.id.desc()).all()]


@app.get("/api/v1/meetings/by-room/{room}")
def get_meeting_by_room(room: str, user=Depends(current_user), db=Depends(get_db)):
    m = db.query(Meeting).filter_by(room_name=room).first()
    if not m:
        raise HTTPException(404, "not found")
    require_host_or_admin(user, m)
    return meeting_out(m)


@app.delete("/api/v1/meetings/{meeting_id}")
def delete_meeting(meeting_id: int, user=Depends(current_user), db=Depends(get_db)):
    require_admin(user)
    m = db.query(Meeting).filter_by(id=meeting_id).first()
    if not m:
        raise HTTPException(404, "not found")
    db.delete(m)
    db.commit()
    return {"ok": True}


@app.post("/api/v1/meetings/{meeting_id}/end")
def end_meeting(meeting_id: int, user=Depends(current_user), db=Depends(get_db)):
    m = db.query(Meeting).filter_by(id=meeting_id).first()
    if not m:
        raise HTTPException(404, "not found")
    require_host_or_admin(user, m)
    m.status = "ended"
    m.ended_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True}


@app.get("/api/v1/meetings/{meeting_id}/lobby")
def list_lobby(meeting_id: int, user=Depends(current_user), db=Depends(get_db)):
    m = db.query(Meeting).filter_by(id=meeting_id).first()
    if not m:
        raise HTTPException(404, "not found")
    require_host_or_admin(user, m)
    reqs = db.query(LobbyRequest).filter_by(room_name=m.room_name).order_by(LobbyRequest.id).all()
    return [
        {"id": r.id, "identity": r.identity, "created_at": r.created_at.isoformat() if r.created_at else None}
        for r in reqs
    ]


@app.post("/api/v1/meetings/{meeting_id}/lobby/{req_id}/admit")
def admit_lobby(meeting_id: int, req_id: int, user=Depends(current_user), db=Depends(get_db)):
    m = db.query(Meeting).filter_by(id=meeting_id).first()
    if not m:
        raise HTTPException(404, "not found")
    require_host_or_admin(user, m)
    lr = db.query(LobbyRequest).filter_by(id=req_id).first()
    if not lr:
        raise HTTPException(404, "request not found")
    lr.status = "admitted"
    db.commit()
    return {"ok": True}


@app.post("/api/v1/meetings/{meeting_id}/lobby/{req_id}/reject")
def reject_lobby(meeting_id: int, req_id: int, user=Depends(current_user), db=Depends(get_db)):
    m = db.query(Meeting).filter_by(id=meeting_id).first()
    if not m:
        raise HTTPException(404, "not found")
    require_host_or_admin(user, m)
    lr = db.query(LobbyRequest).filter_by(id=req_id).first()
    if lr:
        lr.status = "rejected"
        db.commit()
    return {"ok": True}


@app.post("/api/v1/meetings/{meeting_id}/moderate/kick")
def kick_participant(meeting_id: int, req: ModerateRequest, user=Depends(current_user), db=Depends(get_db)):
    m = db.query(Meeting).filter_by(id=meeting_id).first()
    if not m:
        raise HTTPException(404, "not found")
    require_host_or_admin(user, m)
    try:
        remove_participant(m.room_name, req.identity)
    except Exception as e:
        raise HTTPException(502, str(e))
    return {"ok": True}


@app.post("/api/v1/meetings/{meeting_id}/moderate/mute")
def mute_participant_endpoint(meeting_id: int, req: MuteRequest, user=Depends(current_user), db=Depends(get_db)):
    m = db.query(Meeting).filter_by(id=meeting_id).first()
    if not m:
        raise HTTPException(404, "not found")
    require_host_or_admin(user, m)
    try:
        mute_participant(m.room_name, req.identity, req.muted)
    except Exception as e:
        raise HTTPException(502, str(e))
    return {"ok": True}


def _field(obj, snake, camel):
    # LiveKit's Twirp JSON may use proto (snake_case) or JSON (camelCase) names; int64 come as strings.
    value = obj.get(snake, obj.get(camel))
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def live_rooms():
    """{room_name: (participants, started_unix)} for LiveKit rooms that have people in them."""
    out = {}
    for r in list_rooms():
        name = r.get("name")
        if not name:
            continue
        count = _field(r, "num_participants", "numParticipants")
        if count == 0:  # the room summary can lag; ask for the participants themselves
            try:
                count = len(list_participants(name).get("participants") or [])
            except Exception:
                count = 0
        if count > 0:
            out[name] = (count, _field(r, "creation_time", "creationTime"))
    return out


@app.get("/api/v1/meetings/now")
def meetings_now(request: Request, k: str = "", db=Depends(get_db)):
    """Meetings in progress right now, for the page behind the QR on the device screen.

    No login: on the office network anyone can see it; through the internet only with the key
    from the room screen's QR. It lists only meetings their host left visible, and joining still
    needs the passcode and/or the host's lobby approval.
    "In progress" means LiveKit has the room open with at least one participant.
    """
    if from_internet(request) and not any(hmac.compare_digest(k, key) for key in room_keys()):
        raise HTTPException(403, "Scan the QR code on the room screen to see the meetings in this room.")
    try:
        live = live_rooms()
    except Exception:
        raise HTTPException(503, "Meeting server unavailable")
    # Host sign-in stays on the office network: the tunnel never serves /admin.
    admin_url = local_base() + "/admin"
    if not live:
        return {"meetings": [], "admin_url": admin_url}
    rows = (
        db.query(Meeting)
        .filter(Meeting.room_name.in_(list(live)), Meeting.status != "ended", Meeting.show_on_screen.isnot(False))
        .all()
    )
    out = []
    for m in rows:
        count, started = live[m.room_name]
        out.append({
            "title": m.title,
            "room_name": m.room_name,
            "participants": count,
            "started_at": datetime.fromtimestamp(started, timezone.utc).isoformat() if started else None,
            "has_passcode": bool(m.passcode_hash),
            "lobby": bool(m.lobby),
        })
    out.sort(key=lambda x: x["started_at"] or "")
    return {"meetings": out, "admin_url": admin_url}


PASSCODE_FAILURES = {}  # room -> recent failure times; meeting links can be opened from the internet
PASSCODE_MAX_FAILURES, PASSCODE_WINDOW = 10, 600
_passcode_lock = threading.Lock()


def passcode_blocked(room, failed=False):
    with _passcode_lock:
        now = time.time()
        recent = [t for t in PASSCODE_FAILURES.get(room, []) if now - t < PASSCODE_WINDOW]
        if failed:
            recent.append(now)
        if recent:
            PASSCODE_FAILURES[room] = recent
        else:
            PASSCODE_FAILURES.pop(room, None)
        return len(recent) >= PASSCODE_MAX_FAILURES


@app.post("/api/v1/join")
def join(req: JoinRequest, db=Depends(get_db)):
    m = db.query(Meeting).filter_by(room_name=req.room).first()
    if not m:
        raise HTTPException(404, "unknown room")
    if m.locked:
        raise HTTPException(403, "room locked")
    if m.passcode_hash:
        if passcode_blocked(m.room_name):
            raise HTTPException(429, "Too many wrong passcodes. Try again in a few minutes.")
        if not (req.passcode and verify_password(req.passcode, m.passcode_hash)):
            passcode_blocked(m.room_name, failed=True)
            raise HTTPException(401, "invalid passcode")
    if m.lobby:
        existing = db.query(LobbyRequest).filter_by(room_name=m.room_name, identity=req.identity).first()
        if not existing:
            db.add(LobbyRequest(room_name=m.room_name, identity=req.identity))
            db.commit()
        return {"status": "waiting"}
    return issue_livekit_token(req.identity, m.room_name, room_admin=False)


@app.get("/api/v1/lobby/status")
def lobby_status(room: str, identity: str, db=Depends(get_db)):
    lr = db.query(LobbyRequest).filter_by(room_name=room, identity=identity).first()
    if not lr:
        return {"status": "waiting"}
    if lr.status == "admitted":
        token = issue_livekit_token(identity, room, room_admin=False)
        db.delete(lr)
        db.commit()
        return token
    if lr.status == "rejected":
        db.delete(lr)
        db.commit()
        return {"status": "rejected"}
    return {"status": "waiting"}


@app.post("/api/v1/token")
def host_token(req: HostTokenRequest, user=Depends(current_user), db=Depends(get_db)):
    m = db.query(Meeting).filter_by(room_name=req.room).first()
    if not m:
        raise HTTPException(404, "unknown room")
    if user.role not in ("admin", "host"):
        raise HTTPException(403, "host only")
    return issue_livekit_token(req.identity, m.room_name, room_admin=True)


def issue_livekit_token(identity, room, room_admin):
    key, secret = livekit_credentials()
    now = int(time.time())
    payload = {
        "iss": key,
        "sub": identity,
        "nbf": now,
        "exp": now + 3600,
        "metadata": '{"room":"%s"}' % room,
        "video": {
            "roomJoin": True,
            "room": room,
            "canPublish": True,
            "canSubscribe": True,
            "canPublishData": True,
            "roomAdmin": room_admin,
        },
    }
    token = jwt.encode(payload, secret, algorithm="HS256")
    return {"token": token, "ws_url": LIVEKIT_URL}


@app.get("/api/v1/settings")
def get_settings(user=Depends(current_user), db=Depends(get_db)):
    s = db.query(OrgSettings).first()
    if not s:
        return {"org_name": "", "logo_path": "", "theme": {}}
    return {"org_name": s.org_name, "logo_path": s.logo_path, "theme": s.theme}


@app.post("/api/v1/settings")
def set_settings(req: SettingsRequest, user=Depends(current_user), db=Depends(get_db)):
    require_admin(user)
    s = db.query(OrgSettings).first()
    if not s:
        s = OrgSettings()
        db.add(s)
    if req.org_name is not None:
        s.org_name = req.org_name
    if req.logo_path is not None:
        s.logo_path = req.logo_path
    if req.theme is not None:
        s.theme = req.theme
    db.commit()
    return {"ok": True}
