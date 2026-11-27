import base64
import hashlib
import json
import hmac
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
import requests
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

import backup
from models import Base, LobbyRequest, LocalAudit, Meeting, OrgSettings, RecoveryCode, User
from livekit_admin import delete_room, list_participants, list_rooms, mute_participant, remove_participant
from security import (
    create_session_token,
    decode_session_token,
    hash_password,
    livekit_credentials,
    new_totp_secret,
    qr_svg,
    totp_uri,
    verify_password,
    verify_totp,
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
    if os.environ.get("BACKUP_SCHEDULER", "1") == "1":
        threading.Thread(target=backup_scheduler, daemon=True).start()


ADDED_COLUMNS = [  # (table, column, SQL type and default), in the order they were introduced
    ("meetings", "show_on_screen", "BOOLEAN DEFAULT 1"),
    ("users", "token_version", "INTEGER DEFAULT 0"),
    ("org_settings", "screen_reset", "BOOLEAN DEFAULT 1"),
    ("org_settings", "screen_reset_used", "VARCHAR"),
    ("local_audit", "detail", "VARCHAR"),
    ("org_settings", "session_epoch", "VARCHAR"),
    ("users", "totp_pending", "VARCHAR"),
    ("users", "totp_last_step", "INTEGER DEFAULT 0"),
    ("org_settings", "require_2fa", "BOOLEAN DEFAULT 0"),
]


def migrate():
    """Add columns introduced after a device was first provisioned (create_all only adds tables)."""
    insp = inspect(engine)
    for table, column, ddl in ADDED_COLUMNS:
        if column not in {c["name"] for c in insp.get_columns(table)}:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def require_admin(user):
    if user.role != "admin":
        raise HTTPException(403, "admin only")


def require_host_or_admin(user, m):
    if not can_moderate(user, m):
        raise HTTPException(403, "host only")


# What an admin or host may still do while the policy requires two-factor and they have not set it up.
ENROLL_PATHS = {"/api/v1/me", "/api/v1/me/2fa/setup", "/api/v1/me/2fa/enable", "/api/v1/me/password"}


def needs_2fa_enrollment(user, db):
    s = db.query(OrgSettings).first()
    return bool(s and s.require_2fa) and user.role in ("admin", "host") and not user.totp_secret


def current_user(request: Request, authorization: str = Header(None), db=Depends(get_db)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing token")
    try:
        payload = decode_session_token(authorization[7:])
    except Exception:
        raise HTTPException(401, "invalid token")
    user = db.query(User).filter_by(id=int(payload["sub"])).first()
    if not user or not user.active:
        raise HTTPException(401, "unknown user")
    if int(payload.get("v", 0)) != (user.token_version or 0):  # password changed since: sign in again
        raise HTTPException(401, "invalid token")
    if payload.get("e", "") != session_epoch(db):  # the data was restored from a backup since
        raise HTTPException(401, "invalid token")
    if request.url.path not in ENROLL_PATHS and needs_2fa_enrollment(user, db):
        raise HTTPException(403, "2fa enrollment required")
    return user


def session_epoch(db):
    s = db.query(OrgSettings).first()
    return (s.session_epoch if s else None) or ""


def session_token(user, db):
    return create_session_token(user.id, user.token_version or 0, session_epoch(db))


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
    totp: Optional[str] = None


class UserCreate(BaseModel):
    username: str
    password: str
    display_name: str = ""
    role: str = "member"


class MeetingCreate(BaseModel):
    title: str
    scheduled_at: Optional[str] = None
    passcode: Optional[str] = None
    lobby: bool = True
    e2ee: bool = False
    max_participants: int = 10
    duration_minutes: int = 60
    show_on_screen: bool = True


class JoinRequest(BaseModel):
    room: str
    identity: str
    passcode: Optional[str] = None


class HostTokenRequest(BaseModel):
    room: str
    identity: str


class ModerateRequest(BaseModel):
    identity: str


class MuteRequest(BaseModel):
    identity: str
    muted: bool = True


class SettingsRequest(BaseModel):
    org_name: Optional[str] = None
    logo_path: Optional[str] = None
    theme: Optional[dict] = None
    screen_reset: Optional[bool] = None
    require_2fa: Optional[bool] = None


@app.get("/healthz")
def healthz(db=Depends(get_db)):
    # Read by the LCD kiosk: "claimed" decides whether to offer the claim QR; "screen_reset" whether
    # a long press may show an admin reset code, and "screen_reset_used" tells it the code was spent.
    s = db.query(OrgSettings).first()
    return {"ok": True, "claimed": db.query(User).first() is not None,
            "screen_reset": not (s and s.screen_reset is False),
            "screen_reset_used": (s.screen_reset_used if s else None) or ""}


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
        db.flush()
        codes = new_recovery_codes(db, user)  # the Recovery Kit, shown once on the next screen
        audit(db, user, "device.claimed")
        db.commit()
    return {"ok": True, "token": session_token(user, db), "username": username, "recovery_codes": codes}


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
    if user.totp_secret:
        if not req.totp:
            raise HTTPException(401, "totp required")  # the password was right: ask for the code
        step = verify_totp(user.totp_secret, req.totp, user.totp_last_step)
        if step is None:
            fail_login(user)
            db.commit()
            raise HTTPException(401, "invalid totp")
        user.totp_last_step = step
    user.failed_attempts = 0
    user.last_login = datetime.now(timezone.utc)
    db.commit()
    return {"token": session_token(user, db), "must_enroll_2fa": needs_2fa_enrollment(user, db),
            "user": {"id": user.id, "username": user.username, "role": user.role}}


def fail_login(user):
    user.failed_attempts = (user.failed_attempts or 0) + 1
    if user.failed_attempts >= 5:
        user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=15)
        user.failed_attempts = 0


@app.get("/api/v1/me")
def me(user=Depends(current_user), db=Depends(get_db)):
    return {"id": user.id, "username": user.username, "display_name": user.display_name, "role": user.role,
            "totp_enabled": bool(user.totp_secret), "must_enroll_2fa": needs_2fa_enrollment(user, db)}


@app.get("/api/v1/users")
def list_users(user=Depends(current_user), db=Depends(get_db)):
    require_admin(user)
    return [
        {"id": u.id, "username": u.username, "display_name": u.display_name, "role": u.role, "active": u.active,
         "totp_enabled": bool(u.totp_secret)}
        for u in db.query(User).all()
    ]


@app.post("/api/v1/users")
def create_user(req: UserCreate, user=Depends(current_user), db=Depends(get_db)):
    require_admin(user)
    req.username = req.username.strip().lower()
    if not USERNAME_RE.match(req.username):
        raise HTTPException(422, "Username must be 3–32 characters: lowercase letters, numbers, dots, hyphens or underscores.")
    if len(req.password) < MIN_PASSWORD or req.password.lower() == req.username:
        raise HTTPException(422, f"Password must be at least {MIN_PASSWORD} characters and not the username.")
    # A second Client Admin is the easiest way back in when one forgets their password.
    if req.role not in ("admin", "host", "member"):
        raise HTTPException(422, "Role must be admin, host or member.")
    req.display_name = req.display_name.strip()[:80] or req.username
    if db.query(User).filter_by(username=req.username).first():
        raise HTTPException(409, "username exists")
    if db.query(User).count() >= MAX_USERS:
        raise HTTPException(429, f"user limit reached ({MAX_USERS})")
    u = User(username=req.username, display_name=req.display_name, password_hash=hash_password(req.password), role=req.role, active=True)
    db.add(u)
    audit(db, user, "user.created", f"{req.username} ({req.role})")
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
    db.query(RecoveryCode).filter_by(user_id=u.id).delete()
    audit(db, user, "user.removed", u.username)
    db.delete(u)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- passwords and account recovery
#
# Ways back in, lightest first:
#   1. change your own password (signed in)
#   2. a Client Admin resets anyone else's password
#   3. a Recovery Kit code (8 one-time codes, shown once when the device is activated or when an
#      admin makes a new kit) sets a new admin password without signing in
#   4. a code shown on the device's own touch screen after a long press (proof of physical
#      access; the admin can switch this off)
#   5. factory reset
# There is deliberately no reset through the vendor: nothing outside the building can unlock it.
# Every one of these ends the account's other sessions and is written to the device's audit log.

RECOVERY_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # no 0/O or 1/I to misread
RECOVERY_CODES = 8
SCREEN_RESET_FILE = os.environ.get("SCREEN_RESET_FILE", "/etc/appliance/kiosk/admin_reset.json")
SCREEN_RESET_MAX_FAILURES = 5


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class PasswordSet(BaseModel):
    new_password: str


class ConfirmPassword(BaseModel):
    password: str


class RecoveryReset(BaseModel):
    username: str
    code: str
    new_password: str


class ScreenReset(BaseModel):
    username: str
    code: str
    new_password: str


class Limiter:
    """Failures per key within a window, in memory (a restart forgets them, which is fine)."""

    def __init__(self, max_failures, window):
        self.max, self.window, self.hits, self.lock = max_failures, window, {}, threading.Lock()

    def blocked(self, key):
        with self.lock:
            now = time.time()
            recent = [t for t in self.hits.get(key, []) if now - t < self.window]
            self.hits[key] = recent
            return len(recent) >= self.max

    def fail(self, key):
        with self.lock:
            self.hits.setdefault(key, []).append(time.time())

    def clear(self, key):
        with self.lock:
            self.hits.pop(key, None)


RECOVERY_LIMIT = Limiter(5, 900)  # per username
RECOVERY_LIMIT_ALL = Limiter(30, 900)  # across all usernames


def audit(db, actor, action, detail=None):
    db.add(LocalAudit(user_id=actor.id if actor else None, action=action, detail=(detail or "")[:200] or None))


def check_new_password(username, password):
    if len(password) < MIN_PASSWORD:
        raise HTTPException(422, f"Password must be at least {MIN_PASSWORD} characters.")
    if password.lower() == username.lower():
        raise HTTPException(422, "Password must not be the same as the username.")


def set_password(user, password):
    """New password: unlock the account and end every existing session."""
    user.password_hash = hash_password(password)
    user.token_version = (user.token_version or 0) + 1
    user.failed_attempts = 0
    user.locked_until = None


def office_only(request):
    if from_internet(request):
        raise HTTPException(403, "office network only")


def normalize_code(code):
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())


def new_recovery_codes(db, user):
    """Replace the user's Recovery Kit; returns the codes in readable form (XXXXX-XXXXX)."""
    db.query(RecoveryCode).filter_by(user_id=user.id).delete()
    codes = []
    for _ in range(RECOVERY_CODES):
        raw = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(10))
        db.add(RecoveryCode(user_id=user.id, code_hash=hash_password(raw)))
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes


_DUMMY = []


def dummy_hash():
    if not _DUMMY:
        _DUMMY.append(hash_password(secrets.token_hex(8)))
    return _DUMMY[0]


def recovery_remaining(db, user):
    return db.query(RecoveryCode).filter_by(user_id=user.id, used_at=None).count()


@app.post("/api/v1/me/password")
def change_password(req: PasswordChange, user=Depends(current_user), db=Depends(get_db)):
    if not verify_password(req.current_password, user.password_hash):
        raise HTTPException(401, "current password incorrect")
    check_new_password(user.username, req.new_password)
    set_password(user, req.new_password)
    audit(db, user, "password.changed")
    db.commit()
    return {"ok": True, "token": session_token(user, db)}  # this browser stays signed in


@app.post("/api/v1/users/{user_id}/password")
def reset_user_password(user_id: int, req: PasswordSet, user=Depends(current_user), db=Depends(get_db)):
    require_admin(user)
    if user_id == user.id:
        raise HTTPException(400, "use change password for your own account")
    u = db.query(User).filter_by(id=user_id).first()
    if not u:
        raise HTTPException(404, "not found")
    check_new_password(u.username, req.new_password)
    set_password(u, req.new_password)
    audit(db, user, "password.reset_by_admin", u.username)
    db.commit()
    return {"ok": True}


@app.get("/api/v1/me/recovery-codes")
def recovery_status(user=Depends(current_user), db=Depends(get_db)):
    require_admin(user)
    latest = db.query(RecoveryCode).filter_by(user_id=user.id).order_by(RecoveryCode.id.desc()).first()
    return {"remaining": recovery_remaining(db, user), "total": RECOVERY_CODES,
            "created_at": latest.created_at.isoformat() if latest and latest.created_at else None}


@app.post("/api/v1/me/recovery-codes")
def regenerate_recovery_codes(req: ConfirmPassword, user=Depends(current_user), db=Depends(get_db)):
    """A new Recovery Kit; the old codes stop working. Needs the password again."""
    require_admin(user)
    if not verify_password(req.password, user.password_hash):
        raise HTTPException(401, "current password incorrect")
    codes = new_recovery_codes(db, user)
    audit(db, user, "recovery_codes.generated")
    db.commit()
    return {"codes": codes}


@app.post("/api/v1/recovery/reset")
def recovery_reset(req: RecoveryReset, request: Request, db=Depends(get_db)):
    """Forgot password: a Recovery Kit code sets a new one. Office network only, rate limited."""
    office_only(request)
    username = req.username.strip().lower()
    if RECOVERY_LIMIT.blocked(username) or RECOVERY_LIMIT_ALL.blocked("all"):
        raise HTTPException(429, "too many attempts")
    code = normalize_code(req.code)
    user = db.query(User).filter_by(username=username).first()
    match = None
    if user and user.active and user.role == "admin" and len(code) == 10:
        for rc in db.query(RecoveryCode).filter_by(user_id=user.id, used_at=None).all():
            if verify_password(code, rc.code_hash):
                match = rc
                break
    else:
        verify_password(code, dummy_hash())  # same work either way: no hint which usernames exist
    if not match:
        RECOVERY_LIMIT.fail(username)
        RECOVERY_LIMIT_ALL.fail("all")
        raise HTTPException(401, "invalid recovery code")
    check_new_password(username, req.new_password)
    match.used_at = datetime.now(timezone.utc)
    set_password(user, req.new_password)
    had_2fa = clear_2fa(user)
    audit(db, user, "password.reset.recovery_code",
          f"{recovery_remaining(db, user)} codes left" + ("; two-factor turned off" if had_2fa else ""))
    db.commit()
    RECOVERY_LIMIT.clear(username)
    return {"ok": True, "remaining": recovery_remaining(db, user)}


def screen_reset_offer():
    """The code the touch screen is showing, as the kiosk wrote it: a hash, never the code."""
    try:
        with open(SCREEN_RESET_FILE) as f:
            offer = json.load(f)
        if offer.get("expires_at", 0) > time.time():
            return offer
    except (OSError, ValueError):
        pass
    return None


SCREEN_FAILURES = {}


@app.post("/api/v1/recovery/screen")
def screen_reset(req: ScreenReset, request: Request, db=Depends(get_db)):
    """Last resort: the code on the device's touch screen (shown after a long press) sets a new
    admin password. Needs someone at the device and on the office network."""
    office_only(request)
    settings = db.query(OrgSettings).first()
    if settings and settings.screen_reset is False:
        raise HTTPException(403, "screen reset disabled")
    offer = screen_reset_offer()
    if not offer or (settings and settings.screen_reset_used == offer.get("nonce")):
        raise HTTPException(410, "no reset code on screen")
    nonce = offer["nonce"]
    username = req.username.strip().lower()
    user = db.query(User).filter_by(username=username).first()
    code = re.sub(r"\D", "", req.code or "")
    digest = hashlib.sha256((offer["salt"] + code).encode()).hexdigest()
    if not (user and user.active and user.role == "admin" and hmac.compare_digest(digest, offer["hash"])):
        SCREEN_FAILURES[nonce] = SCREEN_FAILURES.get(nonce, 0) + 1
        if SCREEN_FAILURES[nonce] >= SCREEN_RESET_MAX_FAILURES:  # burn the code: hold the screen again
            settings = settings or OrgSettings()
            settings.screen_reset_used = nonce
            db.add(settings)
            audit(db, None, "password.reset.device_screen_burned", "too many wrong codes")
            db.commit()
        raise HTTPException(401, "invalid screen code")
    check_new_password(username, req.new_password)
    settings = settings or OrgSettings()
    settings.screen_reset_used = nonce
    db.add(settings)
    set_password(user, req.new_password)
    had_2fa = clear_2fa(user)
    audit(db, user, "password.reset.device_screen", "two-factor turned off" if had_2fa else None)
    db.commit()
    return {"ok": True}


@app.get("/api/v1/security/events")
def security_events(user=Depends(current_user), db=Depends(get_db)):
    require_admin(user)
    names = {u.id: u.username for u in db.query(User).all()}
    rows = db.query(LocalAudit).order_by(LocalAudit.id.desc()).limit(30).all()
    return [{"at": r.ts.isoformat() if r.ts else None, "action": r.action, "user": names.get(r.user_id), "detail": r.detail}
            for r in rows]


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


def can_moderate(user, m):
    """The Client Admin moderates every meeting; a host only the meetings they created."""
    return user.role == "admin" or (user.role == "host" and m.host_user_id == user.id)


@app.get("/api/v1/meetings")
def list_meetings(user=Depends(current_user), db=Depends(get_db)):
    names = {u.id: u.display_name or u.username for u in db.query(User).all()}
    return [
        {**meeting_out(m), "can_host": can_moderate(user, m), "host_name": names.get(m.host_user_id, "")}
        for m in db.query(Meeting).order_by(Meeting.id.desc()).all()
    ]


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
    room_name = m.room_name
    db.delete(m)
    db.commit()
    close_room(room_name)
    return {"ok": True}


def close_room(room_name):
    """Disconnect everyone still in an ended or deleted meeting (best effort: LiveKit may be down,
    and a room nobody joined does not exist there)."""
    try:
        delete_room(room_name)
    except Exception:
        pass


@app.post("/api/v1/meetings/{meeting_id}/end")
def end_meeting(meeting_id: int, user=Depends(current_user), db=Depends(get_db)):
    m = db.query(Meeting).filter_by(id=meeting_id).first()
    if not m:
        raise HTTPException(404, "not found")
    require_host_or_admin(user, m)
    m.status = "ended"
    m.ended_at = datetime.now(timezone.utc)
    db.commit()
    close_room(m.room_name)
    return {"ok": True}


@app.get("/api/v1/meetings/{meeting_id}/lobby")
def list_lobby(meeting_id: int, user=Depends(current_user), db=Depends(get_db)):
    m = db.query(Meeting).filter_by(id=meeting_id).first()
    if not m:
        raise HTTPException(404, "not found")
    require_host_or_admin(user, m)
    reqs = db.query(LobbyRequest).filter_by(room_name=m.room_name, status="waiting").order_by(LobbyRequest.id).all()
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
    # The request must be waiting in this meeting: a host cannot admit someone into another meeting.
    lr = db.query(LobbyRequest).filter_by(id=req_id, room_name=m.room_name).first()
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
    lr = db.query(LobbyRequest).filter_by(id=req_id, room_name=m.room_name).first()
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


@app.get("/api/v1/meeting-info")
def meeting_info(room: str, db=Depends(get_db)):
    """What a guest's join page shows before joining: the meeting's title and what joining takes.
    Only for people who already have the link (the room name is the secret part of it)."""
    m = db.query(Meeting).filter_by(room_name=room).first()
    if not m or m.status == "ended":
        raise HTTPException(404, "This meeting link is not valid or the meeting has ended.")
    org = db.query(OrgSettings).first()
    return {
        "title": m.title,
        "org_name": (org.org_name if org else "") or "",
        "scheduled_at": m.scheduled_at.isoformat() if m.scheduled_at else None,
        "duration_minutes": m.duration_minutes,
        "has_passcode": bool(m.passcode_hash),
        "lobby": bool(m.lobby),
        "locked": bool(m.locked),
        "e2ee": bool(m.e2ee),  # the key is never here: it is only in the invitation link
    }


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
        elif existing.status != "waiting":
            existing.status = "waiting"
            db.commit()
        return {"status": "waiting"}
    return issue_livekit_token(req.identity, m.room_name, room_admin=False)


@app.get("/api/v1/lobby/status")
def lobby_status(room: str, identity: str, db=Depends(get_db)):
    lr = db.query(LobbyRequest).filter_by(room_name=room, identity=identity).first()
    if not lr:
        return {"status": "waiting"}
    if lr.status == "admitted":
        return issue_livekit_token(identity, room, room_admin=False)
    if lr.status == "rejected":
        return {"status": "rejected"}
    return {"status": "waiting"}


@app.post("/api/v1/token")
def signed_in_token(req: HostTokenRequest, user=Depends(current_user), db=Depends(get_db)):
    """Joining from the console. The meeting's host (or the Client Admin) joins as moderator; any
    other signed-in account (staff) joins straight in, without passcode or waiting room, but
    cannot moderate."""
    m = db.query(Meeting).filter_by(room_name=req.room).first()
    if not m:
        raise HTTPException(404, "unknown room")
    if m.status == "ended":
        raise HTTPException(410, "This meeting has ended.")
    moderator = can_moderate(user, m)
    if m.locked and not moderator:
        raise HTTPException(403, "room locked")
    identity = req.identity.strip()[:60] or user.display_name or user.username
    return {**issue_livekit_token(identity, m.room_name, room_admin=moderator), "host": moderator}


def issue_livekit_token(identity, room, room_admin):
    key, secret = livekit_credentials()
    now = int(time.time())
    payload = {
        "iss": key,
        "sub": identity,
        "nbf": now,
        "exp": now + 3600,
        "metadata": json.dumps({"room": room, "host": bool(room_admin)}),
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
        return {"org_name": "", "logo_path": "", "theme": {}, "screen_reset": True, "require_2fa": False}
    return {"org_name": s.org_name, "logo_path": s.logo_path, "theme": s.theme, "screen_reset": s.screen_reset is not False,
            "require_2fa": bool(s.require_2fa)}


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
    if req.require_2fa is not None and req.require_2fa != bool(s.require_2fa):
        s.require_2fa = req.require_2fa
        audit(db, user, "2fa.required" if req.require_2fa else "2fa.not_required")
    if req.screen_reset is not None and req.screen_reset != (s.screen_reset is not False):
        s.screen_reset = req.screen_reset
        audit(db, user, "screen_reset.enabled" if req.screen_reset else "screen_reset.disabled")
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- encrypted off-site backup (opt-in)
#
# See backup.py. Turning it on needs the admin's password and shows the Backup Key once; the device
# keeps the key (outside the database, so a backup never carries its own key) and backs up daily.
# Restoring needs the Backup Key and the signed-in admin's password, replaces every account,
# meeting and setting, and ends every session (a new session epoch).

DB_PATH = DATABASE_URL.split("sqlite:///", 1)[1] if DATABASE_URL.startswith("sqlite:///") else ""
_backup_lock = threading.Lock()


class BackupRestore(BaseModel):
    backup_key: str
    password: str


def backup_remote():
    return backup.Remote(CONTROL_PLANE_URL, DEVICE_ID)


def backup_scheduler():
    """Daily backup while it is switched on (first check a few minutes after start)."""
    time.sleep(300)
    while True:
        try:
            if backup.due():
                with _backup_lock:
                    backup.run(DB_PATH, backup_remote())
        except Exception as e:  # never let the thread die
            print("backup scheduler:", e, flush=True)
        time.sleep(1800)


@app.get("/api/v1/backup")
def backup_status(user=Depends(current_user)):
    require_admin(user)
    return {**backup.public_status(backup.load_state()), "available": bool(CONTROL_PLANE_URL and DEVICE_ID)}


@app.post("/api/v1/backup/enable")
def backup_enable(req: ConfirmPassword, user=Depends(current_user), db=Depends(get_db)):
    require_admin(user)
    if not verify_password(req.password, user.password_hash):
        raise HTTPException(401, "current password incorrect")
    with _backup_lock:
        if backup.load_state().get("enabled"):
            raise HTTPException(409, "backup already on")
        locator, key = backup.new_key()
        backup.save_state({"enabled": True, "locator": locator, "key": base64.b64encode(key).decode()})
        audit(db, user, "backup.enabled")
        db.commit()
        status = backup.run(DB_PATH, backup_remote())
    return {"backup_key": backup.encode_key(locator, key), "status": status}


@app.post("/api/v1/backup/run")
def backup_now(user=Depends(current_user)):
    require_admin(user)
    with _backup_lock:
        try:
            return backup.run(DB_PATH, backup_remote())
        except backup.BackupError as e:
            raise HTTPException(409, str(e))


@app.post("/api/v1/backup/disable")
def backup_disable(req: ConfirmPassword, user=Depends(current_user), db=Depends(get_db)):
    """Switch off and delete the copies kept off-site (best effort if the service is unreachable)."""
    require_admin(user)
    if not verify_password(req.password, user.password_hash):
        raise HTTPException(401, "current password incorrect")
    with _backup_lock:
        state = backup.load_state()
        removed = True
        if state.get("locator"):
            try:
                backup_remote().delete(state["locator"])
            except backup.BackupError:
                removed = False
        backup.forget_state()
        audit(db, user, "backup.disabled", None if removed else "off-site copies could not be deleted")
        db.commit()
    return {"ok": True, "remote_deleted": removed}


@app.post("/api/v1/backup/restore")
def backup_restore(req: BackupRestore, user=Depends(current_user), db=Depends(get_db)):
    require_admin(user)
    if not verify_password(req.password, user.password_hash):
        raise HTTPException(401, "current password incorrect")
    try:
        locator, key = backup.decode_key(req.backup_key)
    except backup.BackupError as e:
        raise HTTPException(422, str(e))
    if not DB_PATH:
        raise HTTPException(503, "restore needs the device database")
    who = user.username
    with _backup_lock:
        try:
            path = backup.download(backup_remote(), locator, key)
        except backup.BackupError as e:
            raise HTTPException(409, str(e))
        db.close()
        engine.dispose()
        os.replace(path, DB_PATH)
        migrate()  # the backup may predate some columns
        fresh = SessionLocal()
        try:
            settings = fresh.query(OrgSettings).first() or OrgSettings()
            settings.session_epoch = secrets.token_hex(8)  # every session, old or restored, ends
            fresh.add(settings)
            fresh.add(LocalAudit(user_id=None, action="backup.restored", detail=f"by {who}"))
            fresh.commit()
            admins = fresh.query(User).filter_by(role="admin", active=True).count()
        finally:
            fresh.close()
        # Carry on backing up the restored data to the same place, with the same key.
        backup.save_state({"enabled": True, "locator": locator, "key": base64.b64encode(key).decode()})
    return {"ok": True, "admins": admins}



# ---------------------------------------------------------------- two-factor sign-in (TOTP)


class TotpCode(BaseModel):
    code: str


class TotpDisable(BaseModel):
    password: str
    code: str


def clear_2fa(user):
    had = bool(user.totp_secret)
    user.totp_secret = user.totp_pending = None
    user.totp_last_step = 0
    return had


@app.post("/api/v1/me/2fa/setup")
def totp_setup(user=Depends(current_user), db=Depends(get_db)):
    """A new secret to scan; it only takes effect once a code from it is confirmed."""
    if user.totp_secret:
        raise HTTPException(409, "2fa already on")
    user.totp_pending = new_totp_secret()
    db.commit()
    uri = totp_uri(user.totp_pending, user.username)
    return {"secret": user.totp_pending, "uri": uri, "qr_svg": qr_svg(uri)}


@app.post("/api/v1/me/2fa/enable")
def totp_enable(req: TotpCode, user=Depends(current_user), db=Depends(get_db)):
    if not user.totp_pending:
        raise HTTPException(409, "start two-factor setup first")
    step = verify_totp(user.totp_pending, req.code)
    if step is None:
        raise HTTPException(401, "invalid totp")
    user.totp_secret, user.totp_pending, user.totp_last_step = user.totp_pending, None, step
    audit(db, user, "2fa.enabled")
    db.commit()
    return {"ok": True}


@app.post("/api/v1/me/2fa/disable")
def totp_disable(req: TotpDisable, user=Depends(current_user), db=Depends(get_db)):
    if not user.totp_secret:
        raise HTTPException(409, "2fa is off")
    s = db.query(OrgSettings).first()
    if s and s.require_2fa and user.role in ("admin", "host"):
        raise HTTPException(403, "2fa required by policy")
    if not verify_password(req.password, user.password_hash):
        raise HTTPException(401, "current password incorrect")
    if verify_totp(user.totp_secret, req.code, user.totp_last_step) is None:
        raise HTTPException(401, "invalid totp")
    clear_2fa(user)
    audit(db, user, "2fa.disabled")
    db.commit()
    return {"ok": True}


@app.post("/api/v1/users/{user_id}/2fa/reset")
def totp_reset_for(user_id: int, user=Depends(current_user), db=Depends(get_db)):
    """Someone lost their phone: a Client Admin switches their two-factor off (they set it up again)."""
    require_admin(user)
    if user_id == user.id:
        raise HTTPException(400, "use your own two-factor settings")
    u = db.query(User).filter_by(id=user_id).first()
    if not u:
        raise HTTPException(404, "not found")
    clear_2fa(u)
    u.token_version = (u.token_version or 0) + 1  # their sessions end too
    audit(db, user, "2fa.reset_by_admin", u.username)
    db.commit()
    return {"ok": True}
