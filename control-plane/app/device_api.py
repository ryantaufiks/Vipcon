"""Endpoints called by appliances (agent and device app). Never by browsers."""

import json
import logging
import os
import re
import time
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel

import config
from db import get_db
from models import Device, Heartbeat, now
from security import LICENSE_PUBLIC_PEM, decrypt_secret, sha256_hex, verify_device_signature
import cloudflare
from services import (
    audit,
    is_private_ipv4,
    issue_license,
    license_status,
    logo_for_device,
    device_fqdn,
    internet_allowed,
    reconcile_tunnel,
    sync_cert_wanted,
    tagline_for_device,
    tls_bundle,
    tunnel_token,
    turn_config,
    turn_sha256,
)
from web_common import client_ip

router = APIRouter(prefix="/api/v1")
log = logging.getLogger("control-plane.device-api")


def publish_lan_dns(device, lan_ip):
    """Keep the device's name (<site or dev-id>.lan.<root>) pointing at its LAN IP.
    Best effort: retried next heartbeat."""
    if not (cloudflare.enabled() and is_private_ipv4(lan_ip)) or device.dns_ip == lan_ip:
        return
    try:
        cloudflare.upsert_a(device_fqdn(device), lan_ip)
        device.dns_ip = lan_ip
    except cloudflare.CloudflareError as e:
        log.warning("DNS update for %s failed: %s", device.device_id, e)


async def signed_device(request: Request, db=Depends(get_db)):
    """Authenticate a request signed with the device secret (see device/agent/main.py)."""
    device_id = request.headers.get("x-device-id")
    timestamp = request.headers.get("x-timestamp")
    signature = request.headers.get("x-signature")
    if not device_id or not timestamp or not signature:
        raise HTTPException(401, "missing signature headers")
    device = db.query(Device).filter_by(device_id=device_id).first()
    body = await request.body()
    if not device or not verify_device_signature(
        decrypt_secret(device.secret_encrypted), timestamp, signature, request.method, request.url.path, body
    ):
        raise HTTPException(401, "bad signature")
    if device.status == "revoked":
        raise HTTPException(403, "device revoked")
    request.state.body = body
    return device


def _float(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _int(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _dt(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
    except ValueError:
        return None


@router.post("/heartbeat")
def heartbeat(request: Request, device=Depends(signed_device), db=Depends(get_db)):
    try:
        data = json.loads(request.state.body or b"{}")
    except ValueError:
        raise HTTPException(400, "invalid json")
    if not isinstance(data, dict):
        raise HTTPException(400, "invalid json")
    # Metadata only: nothing about users, rooms or participants is accepted here.
    db.add(
        Heartbeat(
            device_id=device.device_id,
            app_version=str(data.get("app_version") or "")[:32] or None,
            cpu_temp=_float(data.get("cpu_temp")),
            disk_pct=_float(data.get("disk_pct")),
            mem_pct=_float(data.get("mem_pct")),
            uptime=_int(data.get("uptime")),
            lan_ip=str(data.get("lan_ip") or "")[:45] or None,
            tunnel_ok=bool(data.get("tunnel_ok")) if data.get("tunnel_ok") is not None else None,
            cert_expires_at=_dt(data.get("cert_expires_at")),
            meetings_count_24h=_int(data.get("meetings_count_24h")),
        )
    )
    first_contact = device.last_seen_at is None
    device.last_seen_at = now()
    publish_lan_dns(device, str(data.get("lan_ip") or ""))
    status, _ = license_status(db, device)
    logo = logo_for_device(db, device)
    bundle = tls_bundle(device_fqdn(device))
    tunnel_ready = reconcile_tunnel(db, device, log)
    token = tunnel_token(device) if tunnel_ready else None
    turn = turn_config(device) if token else None
    db.commit()
    if first_contact:
        sync_cert_wanted(db)  # a new device needs a certificate for its automatic name
    # Display state for the appliance LCD (see device/kiosk).
    return {
        "ok": True,
        "license_status": status,
        "claimed": device.claimed_at is not None,
        "client_name": device.client.name if device.client else None,
        "logo_sha256": logo.sha256 if logo else None,
        "tagline": tagline_for_device(db, device),
        # Office-network name and its certificate (fetched from /api/v1/tls when the hash changes).
        "site_fqdn": device_fqdn(device),
        "tls_sha256": bundle[2] if bundle else None,
        # Internet access: the public name and the hash of the tunnel token (fetched from
        # /api/v1/tunnel when it changes). Both null when the device has no internet access.
        "public_fqdn": device.public_dns if token else None,
        "tunnel_sha256": sha256_hex(token) if token else None,
        # Media relay for guests outside the office (fetched from /api/v1/turn when it changes).
        "turn_sha256": turn_sha256(turn) if turn else None,
    }


@router.get("/tunnel")
def tunnel(device=Depends(signed_device), db=Depends(get_db)):
    """The token cloudflared on the device runs its tunnel with."""
    token = tunnel_token(device) if device.public_dns and internet_allowed(db, device) else None
    if not token:
        raise HTTPException(404, "no internet access")
    return {"public_fqdn": device.public_dns, "token": token}


@router.get("/turn")
def turn_relay(device=Depends(signed_device), db=Depends(get_db)):
    """The TURN relay LiveKit on the device hands to browsers (with internet access only)."""
    config = turn_config(device) if device.public_dns and internet_allowed(db, device) else None
    if not config:
        raise HTTPException(404, "no media relay")
    return config


@router.get("/tls")
def tls_certificate(device=Depends(signed_device)):
    """The device's Let's Encrypt certificate and key for its office-network name."""
    bundle = tls_bundle(device_fqdn(device))
    if bundle is None:
        raise HTTPException(404, "no certificate yet")
    chain, key, sha, not_after = bundle
    return {"fqdn": device_fqdn(device), "fullchain": chain, "privkey": key,
            "sha256": sha, "not_after": not_after.isoformat()}


@router.get("/branding/logo")
def branding_logo(device=Depends(signed_device), db=Depends(get_db)):
    logo = logo_for_device(db, device)
    if logo is None:
        raise HTTPException(404, "no logo")
    return Response(logo.png, media_type="image/png", headers={"X-Logo-SHA256": logo.sha256})


@router.post("/license")
def license_token(device=Depends(signed_device), db=Depends(get_db)):
    token, payload = issue_license(db, device)
    db.commit()
    return {"license_token": token, "status": payload["status"], "expires_at": payload["exp"]}


@router.get("/license/pubkey", response_class=PlainTextResponse)
def license_pubkey():
    return LICENSE_PUBLIC_PEM


class ClaimVerify(BaseModel):
    device_id: str
    claim_code: str


@router.post("/claim/verify")
def claim_verify(req: ClaimVerify, request: Request, db=Depends(get_db)):
    device = db.query(Device).filter_by(device_id=req.device_id).first()
    # One generic error so the endpoint does not reveal which device ids exist.
    if not device or not device.claim_code_hash or device.claim_code_hash != sha256_hex(req.claim_code):
        raise HTTPException(401, "invalid claim")
    if device.status in ("suspended", "revoked"):
        raise HTTPException(403, f"device {device.status}")
    if device.claimed_at is not None:
        raise HTTPException(409, "already claimed")
    device.claimed_at = now()
    device.status = "claimed"
    device.claim_code_hash = None  # one-time use
    audit(db, "device", "device.claimed", device.device_id, client_ip(request))
    db.commit()
    return {"ok": True, "device_id": device.device_id}


# ---------------------------------------------------------------- device data backups
#
# The device seals its database before upload (AES-256-GCM, key held by the client only); this
# server only ever sees "VBK1" + ciphertext. A backup lives under its locator: 32 random hex digits
# that are part of the client's Backup Key, so whoever can fetch it also needs that key to read it.
# Any registered (signed, not revoked) device may write or read a locator it knows: a replacement
# device restores and then carries on backing up to the same place.

LOCATOR_RE = re.compile(r"^[0-9a-f]{32}$")
BACKUP_NAME_RE = re.compile(r"^[0-9]{13}\.vbk$")


def _backup_dir(locator):
    if not LOCATOR_RE.match(locator or ""):
        raise HTTPException(404, "unknown backup")
    return os.path.join(config.DEVICE_BACKUP_DIR, locator)


def _versions(folder):
    try:
        return sorted((n for n in os.listdir(folder) if BACKUP_NAME_RE.match(n)), reverse=True)
    except OSError:
        return []


@router.post("/backup/{locator}")
async def store_backup(locator: str, request: Request, device=Depends(signed_device), db=Depends(get_db)):
    folder = _backup_dir(locator)
    body = request.state.body
    if len(body) > config.DEVICE_BACKUP_MAX_BYTES:
        raise HTTPException(413, "backup too large")
    if not body.startswith(b"VBK1") or len(body) < 4 + 12 + 16:
        raise HTTPException(400, "not a sealed backup")
    os.makedirs(folder, mode=0o700, exist_ok=True)
    name = f"{int(time.time() * 1000)}.vbk"
    tmp = os.path.join(folder, name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(body)
    os.replace(tmp, os.path.join(folder, name))
    for old in _versions(folder)[config.DEVICE_BACKUP_KEEP:]:
        os.remove(os.path.join(folder, old))
    with open(os.path.join(folder, "last_writer"), "w") as f:
        f.write(device.device_id)
    audit(db, f"device:{device.device_id}", "device.backup_stored", locator[:8], client_ip(request))
    db.commit()
    return {"ok": True, "name": name, "size": len(body)}


@router.get("/backup/{locator}")
def list_backups(locator: str, device=Depends(signed_device)):
    folder = _backup_dir(locator)
    return {"versions": [{"name": n, "size": os.path.getsize(os.path.join(folder, n))} for n in _versions(folder)]}


@router.get("/backup/{locator}/{name}")
def get_backup(locator: str, name: str, device=Depends(signed_device)):
    folder = _backup_dir(locator)
    if not BACKUP_NAME_RE.match(name) or not os.path.exists(os.path.join(folder, name)):
        raise HTTPException(404, "unknown backup")
    with open(os.path.join(folder, name), "rb") as f:
        return Response(content=f.read(), media_type="application/octet-stream")


@router.delete("/backup/{locator}")
def delete_backups(locator: str, request: Request, device=Depends(signed_device), db=Depends(get_db)):
    """The client switched backups off: remove every copy kept here."""
    folder = _backup_dir(locator)
    removed = 0
    for n in os.listdir(folder) if os.path.isdir(folder) else []:
        os.remove(os.path.join(folder, n))
        removed += 1
    if os.path.isdir(folder):
        os.rmdir(folder)
    audit(db, f"device:{device.device_id}", "device.backup_deleted", locator[:8], client_ip(request))
    db.commit()
    return {"ok": True, "removed": removed}
