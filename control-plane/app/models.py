from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def now():
    return datetime.now(timezone.utc)


class Plan(Base):
    __tablename__ = "plans"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    price_monthly = Column(Integer)
    price_yearly = Column(Integer)
    max_participants = Column(Integer, default=10)
    max_users = Column(Integer, default=10)
    connectivity_mode = Column(String, default="standard")
    features = Column(JSON)
    active = Column(Boolean, default=True)


class Client(Base):
    __tablename__ = "clients"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    contact_email = Column(String)
    contact_phone = Column(String)
    notes = Column(Text)
    created_at = Column(DateTime, default=now)


class Device(Base):
    __tablename__ = "devices"
    id = Column(Integer, primary_key=True)
    device_id = Column(String, unique=True, nullable=False, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=True)
    plan_id = Column(Integer, ForeignKey("plans.id"), nullable=True)
    device_secret = Column(String)
    claim_code_hash = Column(String)
    claimed_at = Column(DateTime, nullable=True)
    connectivity_mode = Column(String, default="standard")
    status = Column(String, default="provisioned")
    qr_version = Column(Integer, default=1)
    created_at = Column(DateTime, default=now)


class Contract(Base):
    __tablename__ = "contracts"
    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"))
    device_id = Column(Integer, ForeignKey("devices.id"))
    plan_id = Column(Integer, ForeignKey("plans.id"))
    start_date = Column(DateTime)
    end_date = Column(DateTime)
    status = Column(String, default="active")
    price_agreed = Column(Integer)


class Heartbeat(Base):
    __tablename__ = "heartbeats"
    id = Column(Integer, primary_key=True)
    device_id = Column(String, index=True)
    ts = Column(DateTime, default=now)
    app_version = Column(String)
    cpu_temp = Column(String)
    disk_pct = Column(String)
    mem_pct = Column(String)
    uptime = Column(Integer)
    lan_ip = Column(String)
    tunnel_ok = Column(Boolean)
    meetings_count_24h = Column(Integer)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    actor = Column(String)
    action = Column(String)
    target = Column(String)
    ts = Column(DateTime, default=now)
    ip = Column(String)
    detail = Column(JSON)


class SuperAdmin(Base):
    __tablename__ = "superadmins"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False)
    password_hash = Column(String)
    totp_secret = Column(String)
    created_at = Column(DateTime, default=now)
