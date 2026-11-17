import base64
import hashlib
import hmac
import os
import secrets
import time

import jwt
import pyotp
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from config import LICENSE_KEY_PATH, SECRET_KEY, SESSION_HOURS, SIGNATURE_MAX_SKEW

# ---------------------------------------------------------------- passwords


def hash_password(password):
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return "scrypt$%s$%s" % (base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def verify_password(password, stored):
    try:
        _, salt_b64, dk_b64 = stored.split("$")
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), n=16384, r=8, p=1, dklen=32)
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except Exception:
        return False


def sha256_hex(value):
    return hashlib.sha256(value.encode()).hexdigest()


# ---------------------------------------------------------------- TOTP


def new_totp_secret():
    return pyotp.random_base32()


def totp_uri(secret, email):
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name="Vipcon Control")


def verify_totp(secret, code, last_step):
    """Return the matched time step, or None. A step at or before last_step is a replay."""
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit():
        return None
    totp = pyotp.TOTP(secret)
    now_step = int(time.time()) // 30
    for step in (now_step - 1, now_step, now_step + 1):
        if step > (last_step or 0) and hmac.compare_digest(totp.at(step * 30), code):
            return step
    return None


# ---------------------------------------------------------------- sessions

SESSION_COOKIE = "vipcon_cp_session"


def create_session(admin_id):
    now = int(time.time())
    payload = {
        "sub": str(admin_id),
        "iat": now,
        "exp": now + SESSION_HOURS * 3600,
        "csrf": secrets.token_urlsafe(24),
        "aud": "superadmin",
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def decode_session(token):
    return jwt.decode(token, SECRET_KEY, algorithms=["HS256"], audience="superadmin")


# ---------------------------------------------------------------- device secrets at rest

_fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256((SECRET_KEY + ":device-secret").encode()).digest()))


def encrypt_secret(value):
    return _fernet.encrypt(value.encode()).decode()


def decrypt_secret(value):
    return _fernet.decrypt(value.encode()).decode()


# ---------------------------------------------------------------- signed device requests


def device_signature(secret, timestamp, method, path, body):
    message = b"\n".join([str(timestamp).encode(), method.upper().encode(), path.encode(), body])
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_device_signature(secret, timestamp, signature, method, path, body):
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs(int(time.time()) - ts) > SIGNATURE_MAX_SKEW:
        return False
    expected = device_signature(secret, ts, method, path, body)
    return hmac.compare_digest(expected, signature or "")


# ---------------------------------------------------------------- license signing key


def _load_or_create_license_key():
    if os.path.exists(LICENSE_KEY_PATH):
        with open(LICENSE_KEY_PATH, "rb") as f:
            return serialization.load_pem_private_key(f.read(), password=None)
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    directory = os.path.dirname(os.path.abspath(LICENSE_KEY_PATH))
    os.makedirs(directory, exist_ok=True)
    fd = os.open(LICENSE_KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    return key


LICENSE_PRIVATE_KEY = _load_or_create_license_key()
LICENSE_PUBLIC_PEM = (
    LICENSE_PRIVATE_KEY.public_key()
    .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    .decode()
)


def sign_license(payload):
    return jwt.encode(payload, LICENSE_PRIVATE_KEY, algorithm="EdDSA")
