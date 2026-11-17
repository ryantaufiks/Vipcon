from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

CONNECTIVITY_MODES = ("standard", "sovereign")
DEVICE_STATUSES = ("provisioned", "claimed", "suspended", "revoked")
CONTRACT_STATUSES = ("active", "suspended", "expired")


def now():
    return datetime.now(timezone.utc)


def aware(dt):
    """SQLite drops tzinfo; treat stored naive datetimes as UTC."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class Plan(Base):
    __tablename__ = "plans"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    price_monthly = Column(Integer, default=0)
    price_yearly = Column(Integer, default=0)
    max_participants = Column(Integer, default=10)
    max_users = Column(Integer, default=20)
    max_concurrent_meetings = Column(Integer, default=5)
    connectivity_mode = Column(String, default="standard")
    features = Column(JSON, default=dict)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=now)


class Client(Base):
    __tablename__ = "clients"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    contact_email = Column(String)
    contact_phone = Column(String)
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), default=now)


class Device(Base):
    __tablename__ = "devices"
    id = Column(Integer, primary_key=True)
    device_id = Column(String, unique=True, nullable=False, index=True)
    label = Column(String)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=True)
    plan_id = Column(Integer, ForeignKey("plans.id"), nullable=True)
    # Encrypted with a key derived from SECRET_KEY; needed to verify device HMACs.
    secret_encrypted = Column(String, nullable=False)
    claim_code_hash = Column(String)
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    connectivity_mode = Column(String, default="standard")
    status = Column(String, default="provisioned")
    qr_version = Column(Integer, default=1)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=now)

    client = relationship("Client")
    plan = relationship("Plan")


class BrandingLogo(Base):
    """Logo shown on the appliance LCD. client_id NULL is the global default."""

    __tablename__ = "branding_logos"
    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=True, unique=True)
    png = Column(LargeBinary, nullable=False)
    sha256 = Column(String(64), nullable=False)
    width = Column(Integer)
    height = Column(Integer)
    updated_at = Column(DateTime(timezone=True), default=now, onupdate=now)


class Contract(Base):
    __tablename__ = "contracts"
    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False)
    plan_id = Column(Integer, ForeignKey("plans.id"), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    status = Column(String, default="active")
    price_agreed = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=now)

    client = relationship("Client")
    device = relationship("Device")
    plan = relationship("Plan")


class Heartbeat(Base):
    __tablename__ = "heartbeats"
    id = Column(Integer, primary_key=True)
    device_id = Column(String, index=True)
    ts = Column(DateTime(timezone=True), default=now, index=True)
    app_version = Column(String)
    cpu_temp = Column(Float)
    disk_pct = Column(Float)
    mem_pct = Column(Float)
    uptime = Column(Integer)
    lan_ip = Column(String)
    tunnel_ok = Column(Boolean)
    cert_expires_at = Column(DateTime(timezone=True), nullable=True)
    meetings_count_24h = Column(Integer)


class LicenseToken(Base):
    __tablename__ = "license_tokens"
    id = Column(Integer, primary_key=True)
    device_id = Column(String, index=True)
    jti = Column(String, unique=True)
    issued_at = Column(DateTime(timezone=True), default=now)
    expires_at = Column(DateTime(timezone=True))
    status = Column(String)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    actor = Column(String)
    action = Column(String)
    target = Column(String)
    ts = Column(DateTime(timezone=True), default=now, index=True)
    ip = Column(String)
    detail = Column(JSON)


class SuperAdmin(Base):
    __tablename__ = "superadmins"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    totp_secret = Column(String, nullable=False)
    active = Column(Boolean, default=True)
    failed_attempts = Column(Integer, default=0)
    locked_until = Column(DateTime(timezone=True), nullable=True)
    last_totp_step = Column(Integer, default=0)
    last_login = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=now)
