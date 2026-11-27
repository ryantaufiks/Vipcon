from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def now():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    display_name = Column(String)
    password_hash = Column(String, nullable=False)
    role = Column(String, default="member")
    totp_secret = Column(String, nullable=True)
    active = Column(Boolean, default=True)
    failed_attempts = Column(Integer, default=0)
    locked_until = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=now)
    last_login = Column(DateTime, nullable=True)
    # Bumped whenever the password changes: sessions carry it, so older ones stop working.
    token_version = Column(Integer, default=0)


class RecoveryCode(Base):
    """One-time codes that let an admin set a new password without signing in (the Recovery
    Kit). Stored hashed, like passwords; a used code is kept, marked, so it can never work again."""
    __tablename__ = "recovery_codes"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    code_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=now)
    used_at = Column(DateTime, nullable=True)


class Meeting(Base):
    __tablename__ = "meetings"
    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    room_name = Column(String, unique=True, nullable=False)
    passcode_hash = Column(String, nullable=True)
    host_user_id = Column(Integer, ForeignKey("users.id"))
    scheduled_at = Column(DateTime, nullable=True)
    duration_minutes = Column(Integer, default=60)
    lobby = Column(Boolean, default=True)
    e2ee = Column(Boolean, default=False)
    locked = Column(Boolean, default=False)
    max_participants = Column(Integer, default=10)
    # Listed on the room's "meetings in progress" page (the QR on the device screen).
    show_on_screen = Column(Boolean, default=True)
    status = Column(String, default="scheduled")
    created_at = Column(DateTime, default=now)
    ended_at = Column(DateTime, nullable=True)


class LobbyRequest(Base):
    __tablename__ = "lobby_requests"
    id = Column(Integer, primary_key=True)
    room_name = Column(String, index=True)
    identity = Column(String)
    status = Column(String, default="waiting")
    created_at = Column(DateTime, default=now)


class Session(Base):
    __tablename__ = "sessions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    token_hash = Column(String, unique=True)
    expires_at = Column(DateTime)
    ip = Column(String)


class OrgSettings(Base):
    __tablename__ = "org_settings"
    id = Column(Integer, primary_key=True)
    org_name = Column(String)
    logo_path = Column(String)
    theme = Column(JSON)
    # Last-resort admin reset from the device's own touch screen (proof of physical access).
    screen_reset = Column(Boolean, default=True)
    screen_reset_used = Column(String, nullable=True)  # nonce of the last screen code spent
    # Part of every session token; a restore from backup sets a new one, ending every session.
    session_epoch = Column(String, nullable=True)


class LicenseState(Base):
    __tablename__ = "license_state"
    id = Column(Integer, primary_key=True)
    token_encrypted = Column(String)
    expires_at = Column(DateTime)
    last_checked = Column(DateTime)


class LocalAudit(Base):
    __tablename__ = "local_audit"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=True)
    action = Column(String)
    ts = Column(DateTime, default=now)
    detail = Column(String, nullable=True)
