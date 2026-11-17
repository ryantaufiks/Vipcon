import base64
import hashlib
import hmac
import io
import json
import os
import secrets
import time
import uuid
from datetime import datetime, timezone

import jwt
import qrcode
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, Device, Heartbeat

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./control.db")
ROOT_DOMAIN = os.environ.get("ROOT_DOMAIN", "domainutama.com")
LICENSE_SECRET = os.environ.get("LICENSE_SECRET", secrets.token_hex(32))

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine)

app = FastAPI(title="VIP VC Control Plane")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def sha256_hex(s):
    return hashlib.sha256(s.encode()).hexdigest()


def make_qr_data_uri(url):
    buf = io.BytesIO()
    img = qrcode.make(url)
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


@app.on_event("startup")
def startup():
    Base.metadata.create_all(engine)


@app.post("/api/v1/devices")
def create_device(db=Depends(get_db)):
    device_id = "dev_" + uuid.uuid4().hex[:12]
    device_secret = secrets.token_hex(32)
    claim_code = secrets.token_urlsafe(24)
    device = Device(
        device_id=device_id,
        device_secret=device_secret,
        claim_code_hash=sha256_hex(claim_code),
        status="provisioned",
    )
    db.add(device)
    db.commit()
    claim_url = f"https://{device_id}.{ROOT_DOMAIN}/claim?c={claim_code}"
    return {
        "device_id": device_id,
        "device_secret": device_secret,
        "claim_code": claim_code,
        "claim_url": claim_url,
        "qr": make_qr_data_uri(claim_url),
    }


def verify_hmac(device, sig, body):
    expected = hmac.new(device.device_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


@app.post("/api/v1/heartbeat")
async def heartbeat(
    request: Request,
    x_device_id: str = Header(None),
    x_signature: str = Header(None),
    db=Depends(get_db),
):
    body = await request.body()
    if not x_device_id or not x_signature:
        raise HTTPException(401, "missing headers")
    device = db.query(Device).filter_by(device_id=x_device_id).first()
    if not device or not verify_hmac(device, x_signature, body):
        raise HTTPException(401, "bad signature")
    data = json.loads(body)
    hb = Heartbeat(
        device_id=data["device_id"],
        app_version=data.get("app_version"),
        cpu_temp=data.get("cpu_temp"),
        disk_pct=data.get("disk_pct"),
        mem_pct=data.get("mem_pct"),
        lan_ip=data.get("lan_ip"),
        tunnel_ok=data.get("tunnel_ok"),
        meetings_count_24h=data.get("meetings_count_24h"),
    )
    db.add(hb)
    db.commit()
    return {"ok": True}


@app.post("/api/v1/claim/verify")
def claim_verify(
    device_id: str = Body(...),
    claim_code: str = Body(...),
    db=Depends(get_db),
):
    device = db.query(Device).filter_by(device_id=device_id).first()
    if not device:
        raise HTTPException(404, "unknown device")
    if device.claimed_at is not None:
        raise HTTPException(409, "already claimed")
    if not device.claim_code_hash or not hmac.compare_digest(device.claim_code_hash, sha256_hex(claim_code)):
        raise HTTPException(401, "invalid claim code")
    device.claimed_at = datetime.now(timezone.utc)
    device.status = "claimed"
    db.commit()
    return {"ok": True, "device_id": device_id}


@app.get("/api/v1/license/{device_id}")
def license_token(device_id: str, db=Depends(get_db)):
    device = db.query(Device).filter_by(device_id=device_id).first()
    if not device:
        raise HTTPException(404, "unknown device")
    now = int(time.time())
    ttl = 7 * 86400
    token = jwt.encode(
        {
            "iss": "control-plane",
            "sub": device_id,
            "nbf": now,
            "exp": now + ttl,
            "jti": uuid.uuid4().hex,
        },
        LICENSE_SECRET,
        algorithm="HS256",
    )
    return {"license_token": token, "expires_in": ttl}
