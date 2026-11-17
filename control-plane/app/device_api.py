"""Endpoints called by appliances (agent and device app). Never by browsers."""

import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel

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
    site_fqdn,
    tagline_for_device,
    tls_bundle,
)
from web_common import client_ip

router = APIRouter(prefix="/api/v1")
log = logging.getLogger("control-plane.device-api")


def publish_lan_dns(device, lan_ip):
    """Keep <site>.lan.<root> pointing at the device's LAN IP. Best effort: retried next heartbeat."""
    if not (device.site_id and cloudflare.enabled() and is_private_ipv4(lan_ip)) or device.dns_ip == lan_ip:
        return
    try:
        cloudflare.upsert_a(site_fqdn(device.site_id), lan_ip)
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
    device.last_seen_at = now()
    publish_lan_dns(device, str(data.get("lan_ip") or ""))
    status, _ = license_status(db, device)
    logo = logo_for_device(db, device)
    bundle = tls_bundle(device.site_id)
    db.commit()
    # Display state for the appliance LCD (see device/kiosk).
    return {
        "ok": True,
        "license_status": status,
        "claimed": device.claimed_at is not None,
        "client_name": device.client.name if device.client else None,
        "logo_sha256": logo.sha256 if logo else None,
        "tagline": tagline_for_device(db, device),
        # Office-network name and its certificate (fetched from /api/v1/tls when the hash changes).
        "site_fqdn": site_fqdn(device.site_id),
        "tls_sha256": bundle[2] if bundle else None,
    }


@router.get("/tls")
def tls_certificate(device=Depends(signed_device)):
    """The device's Let's Encrypt certificate and key for its office-network name."""
    bundle = tls_bundle(device.site_id)
    if bundle is None:
        raise HTTPException(404, "no certificate yet")
    chain, key, sha, not_after = bundle
    return {"fqdn": site_fqdn(device.site_id), "fullchain": chain, "privkey": key,
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
