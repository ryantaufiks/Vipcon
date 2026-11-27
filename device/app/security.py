import base64
import hashlib
import hmac
import os
import secrets
import time

import jwt

SESSION_SECRET = os.environ.get("SESSION_SECRET", "")


def hash_password(password):
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return "scrypt$%s$%s" % (base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def verify_password(password, stored):
    try:
        _, salt_b64, dk_b64 = stored.split("$")
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(dk_b64)
        dk = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


def create_session_token(user_id, version=0, epoch=""):
    """version is the user's token_version (a password change bumps it); epoch is the device's
    session epoch (a restore from backup changes it). Either change ends older sessions."""
    now = int(time.time())
    return jwt.encode({"sub": str(user_id), "v": int(version or 0), "e": epoch or "", "nbf": now, "exp": now + 86400},
                      SESSION_SECRET, algorithm="HS256")


def decode_session_token(token):
    return jwt.decode(token, SESSION_SECRET, algorithms=["HS256"])


def read(path):
    with open(path) as f:
        return f.read().strip()


def livekit_credentials():
    key = os.environ.get("LIVEKIT_API_KEY") or read("/etc/appliance/livekit_key")
    secret = os.environ.get("LIVEKIT_API_SECRET") or read("/etc/appliance/livekit_secret")
    return key, secret


# ---------------------------------------------------------------- two-factor (TOTP, RFC 6238)
# 30-second steps, 6 digits, SHA-1: what every authenticator app speaks.


def new_totp_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_at(secret, step):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, step.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def verify_totp(secret, code, last_step=0, now=None):
    """The step the code belongs to (one step of clock drift either way), or None. A step at or
    before last_step is refused, so a code that has been used cannot be used again."""
    code = "".join(c for c in str(code or "") if c.isdigit())
    if not secret or len(code) != 6:
        return None
    step = int((now or time.time()) // 30)
    for s in (step - 1, step, step + 1):
        if s > (last_step or 0) and hmac.compare_digest(totp_at(secret, s), code):
            return s
    return None


def totp_uri(secret, username, issuer="VIPCON"):
    from urllib.parse import quote
    return f"otpauth://totp/{quote(issuer)}:{quote(username)}?secret={secret}&issuer={quote(issuer)}&digits=6&period=30"


def qr_svg(text):
    import qrcode
    import qrcode.image.svg
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    return img.to_string(encoding="unicode")
