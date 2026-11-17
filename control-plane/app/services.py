import base64
import hashlib
import io
import json
import secrets
import uuid
from datetime import date, datetime, timedelta, timezone

import qrcode

from config import CONTROL_PLANE_URL, LICENSE_TTL_DAYS, ONLINE_WINDOW_SECONDS, ROOT_DOMAIN
from models import AuditLog, Contract, Device, LicenseToken, aware, now
from security import LICENSE_PUBLIC_PEM, encrypt_secret, sha256_hex, sign_license


def audit(db, actor, action, target="", ip="", **detail):
    db.add(AuditLog(actor=actor, action=action, target=str(target), ip=ip, detail=detail or None))


def new_device_id():
    return "dev_" + uuid.uuid4().hex[:12]


def new_claim_code():
    return secrets.token_urlsafe(24)


def claim_url(device_id, claim_code):
    return f"https://{device_id}.{ROOT_DOMAIN}/claim?c={claim_code}"


def local_url(device_id):
    return f"https://{device_id}.local.{ROOT_DOMAIN}"


def qr_data_uri(text):
    img = qrcode.make(text, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def create_device(db, label, client_id, plan_id, connectivity_mode):
    device_secret = secrets.token_hex(32)
    claim_code = new_claim_code()
    device = Device(
        device_id=new_device_id(),
        label=label or None,
        client_id=client_id,
        plan_id=plan_id,
        secret_encrypted=encrypt_secret(device_secret),
        claim_code_hash=sha256_hex(claim_code),
        connectivity_mode=connectivity_mode,
        status="provisioned",
    )
    db.add(device)
    db.flush()
    return device, device_secret, claim_code


def device_json(device, device_secret, claim_code):
    """The file written to the Pi boot partition; read once by first-boot.sh, then shredded."""
    return json.dumps(
        {
            "device_id": device.device_id,
            "device_secret": device_secret,
            "claim_code": claim_code,
            "connectivity_mode": device.connectivity_mode,
            "root_domain": ROOT_DOMAIN,
            "control_plane_url": CONTROL_PLANE_URL,
            "license_public_key": LICENSE_PUBLIC_PEM,
        },
        indent=2,
    )


def provisioning_bundle(device, device_secret, claim_code):
    url = claim_url(device.device_id, claim_code)
    content = device_json(device, device_secret, claim_code) if device_secret else None
    return {
        "device": device,
        "claim_code": claim_code,
        "claim_url": url,
        "qr": qr_data_uri(url),
        "local_url": local_url(device.device_id),
        "device_json": content,
        "device_json_href": (
            "data:application/json;base64," + base64.b64encode(content.encode()).decode() if content else None
        ),
    }


def is_online(device, at=None):
    seen = aware(device.last_seen_at)
    if not seen:
        return False
    return ((at or now()) - seen).total_seconds() <= ONLINE_WINDOW_SECONDS


def current_contract(db, device, today=None):
    today = today or date.today()
    return (
        db.query(Contract)
        .filter(
            Contract.device_id == device.id,
            Contract.status == "active",
            Contract.start_date <= today,
            Contract.end_date >= today,
        )
        .order_by(Contract.end_date.desc())
        .first()
    )


def license_status(db, device, today=None):
    """Return (status, contract). Only "active" lets a device start new meetings."""
    if device.status == "revoked":
        return "revoked", None
    if device.status == "suspended":
        return "suspended", None
    contract = current_contract(db, device, today)
    if contract:
        return "active", contract
    has_any = db.query(Contract).filter(Contract.device_id == device.id).first() is not None
    return ("expired" if has_any else "no_contract"), None


def issue_license(db, device):
    status, contract = license_status(db, device)
    plan = contract.plan if contract else device.plan
    issued = now()
    ttl = timedelta(days=LICENSE_TTL_DAYS)
    expires = issued + ttl
    if contract:
        # Never license past the contract; the grace window is the device's own decision.
        contract_end = datetime.combine(contract.end_date, datetime.max.time(), tzinfo=timezone.utc)
        expires = min(expires, contract_end)
    jti = uuid.uuid4().hex
    payload = {
        "iss": "vipcon-control-plane",
        "sub": device.device_id,
        "iat": int(issued.timestamp()),
        "nbf": int(issued.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": jti,
        "status": status,
        "connectivity_mode": device.connectivity_mode,
        "contract_end": contract.end_date.isoformat() if contract else None,
        "limits": {
            "max_participants": plan.max_participants,
            "max_users": plan.max_users,
            "max_concurrent_meetings": plan.max_concurrent_meetings,
        }
        if plan
        else None,
        "features": (plan.features or {}) if plan else {},
    }
    db.add(LicenseToken(device_id=device.device_id, jti=jti, issued_at=issued, expires_at=expires, status=status))
    return sign_license(payload), payload


# ---------------------------------------------------------------- branding

LOGO_MAX_UPLOAD = 2 * 1024 * 1024
LOGO_MAX_SIDE = 480  # the LCD is 480x320; anything bigger is wasted bytes on the device


class LogoError(ValueError):
    pass


def normalize_logo(data):
    """Validate an uploaded image and re-encode it as a bounded RGBA PNG.

    Re-encoding drops metadata and anything that is not pixels, so the device
    only ever receives a plain PNG produced here.
    """
    from PIL import Image, UnidentifiedImageError

    if not data:
        raise LogoError("File kosong.")
    if len(data) > LOGO_MAX_UPLOAD:
        raise LogoError("File terlalu besar (maks 2 MB).")
    Image.MAX_IMAGE_PIXELS = 25_000_000
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format not in ("PNG", "JPEG", "WEBP"):
                raise LogoError("Format harus PNG, JPG atau WEBP.")
            probe.verify()
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            img = img.convert("RGBA")
    except LogoError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, SyntaxError):
        raise LogoError("File bukan gambar yang valid.")
    bbox = img.getbbox()  # trim fully transparent borders
    if bbox:
        img = img.crop(bbox)
    img.thumbnail((LOGO_MAX_SIDE, LOGO_MAX_SIDE), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue(), img.width, img.height


def set_logo(db, client_id, data):
    from models import BrandingLogo

    png, width, height = normalize_logo(data)
    logo = db.query(BrandingLogo).filter(BrandingLogo.client_id.is_(None) if client_id is None
                                          else BrandingLogo.client_id == client_id).first()
    if logo is None:
        logo = BrandingLogo(client_id=client_id)
        db.add(logo)
    logo.png, logo.width, logo.height = png, width, height
    logo.sha256 = hashlib.sha256(png).hexdigest()
    logo.updated_at = now()
    db.flush()
    return logo


def get_logo(db, client_id):
    from models import BrandingLogo

    q = db.query(BrandingLogo)
    return (q.filter(BrandingLogo.client_id.is_(None)) if client_id is None
            else q.filter(BrandingLogo.client_id == client_id)).first()


def logo_for_device(db, device):
    """The client's own logo wins; otherwise the global default; otherwise None."""
    return (device.client_id and get_logo(db, device.client_id)) or get_logo(db, None)
