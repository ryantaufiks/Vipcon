import base64
import hashlib
import io
import ipaddress
import json
import os
import re
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
        raise LogoError("The file is empty.")
    if len(data) > LOGO_MAX_UPLOAD:
        raise LogoError("The file is too large (max 2 MB).")
    Image.MAX_IMAGE_PIXELS = 25_000_000
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format not in ("PNG", "JPEG", "WEBP"):
                raise LogoError("The image must be PNG, JPG or WEBP.")
            probe.verify()
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            img = img.convert("RGBA")
    except LogoError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, SyntaxError):
        raise LogoError("The file is not a valid image.")
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


TAGLINE_MAX = 40  # fits one line under the logo on a 480 px panel


def get_tagline(db, client_id):
    from models import BrandingText

    q = db.query(BrandingText)
    return (q.filter(BrandingText.client_id.is_(None)) if client_id is None
            else q.filter(BrandingText.client_id == client_id)).first()


def set_tagline(db, client_id, text):
    """Empty text removes the row: a client then falls back to the default tagline."""
    from models import BrandingText

    text = " ".join((text or "").split())[:TAGLINE_MAX]
    row = get_tagline(db, client_id)
    if not text:
        if row:
            db.delete(row)
        return None
    if row is None:
        row = BrandingText(client_id=client_id)
        db.add(row)
    row.tagline = text
    row.updated_at = now()
    return row


def tagline_for_device(db, device):
    row = (device.client_id and get_tagline(db, device.client_id)) or get_tagline(db, None)
    return row.tagline if row else None


# ---------------------------------------------------------------- site id (office-network name)

SITE_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,28}[a-z0-9])$")
RESERVED_SITE_IDS = {"www", "api", "app", "admin", "control", "lan", "local", "mail", "status", "vipcon", "test"}


def normalize_site_id(raw):
    """'' -> None (no site name); otherwise a valid lowercase DNS label or ValueError."""
    value = (raw or "").strip().lower()
    if not value:
        return None
    if not SITE_ID_RE.match(value) or "--" in value:
        raise ValueError("Site ID: 3-30 characters, lowercase letters, digits and single hyphens; "
                         "must start and end with a letter or digit.")
    if value in RESERVED_SITE_IDS:
        raise ValueError(f"Site ID '{value}' is reserved.")
    return value


def site_fqdn(site_id):
    from config import LAN_LABEL

    return f"{site_id}.{LAN_LABEL}.{ROOT_DOMAIN}" if site_id else None


def is_private_ipv4(value):
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return ip.version == 4 and ip.is_private and not ip.is_loopback and not ip.is_link_local


def sync_cert_wanted(db):
    """Tell the certs worker which names need a certificate (devices with a site, not revoked)."""
    from config import CERTS_WANTED

    names = sorted(site_fqdn(d.site_id) for d in db.query(Device).filter(Device.site_id.isnot(None))
                   if d.status != "revoked")
    try:
        os.makedirs(os.path.dirname(CERTS_WANTED), exist_ok=True)
        tmp = CERTS_WANTED + ".tmp"
        with open(tmp, "w") as f:
            f.write("".join(n + "\n" for n in names))
        os.replace(tmp, CERTS_WANTED)
    except OSError:
        pass  # e.g. tests or a read-only checkout; the worker keeps its last list
    return names


def tls_bundle(site_id):
    """(fullchain_pem, privkey_pem, sha256_of_fullchain, not_after) or None if not issued yet."""
    from config import TLS_DIR

    if not site_id:
        return None
    base = os.path.join(TLS_DIR, site_fqdn(site_id))
    try:
        with open(os.path.join(base, "fullchain.pem")) as f:
            chain = f.read()
        with open(os.path.join(base, "privkey.pem")) as f:
            key = f.read()
    except OSError:
        return None
    from cryptography import x509

    try:
        not_after = x509.load_pem_x509_certificate(chain.encode()).not_valid_after_utc
    except ValueError:
        return None
    return chain, key, hashlib.sha256(chain.encode()).hexdigest(), not_after
