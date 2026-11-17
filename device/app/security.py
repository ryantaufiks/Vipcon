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


def create_session_token(user_id):
    now = int(time.time())
    return jwt.encode({"sub": str(user_id), "nbf": now, "exp": now + 86400}, SESSION_SECRET, algorithm="HS256")


def decode_session_token(token):
    return jwt.decode(token, SESSION_SECRET, algorithms=["HS256"])


def read(path):
    with open(path) as f:
        return f.read().strip()


def livekit_credentials():
    key = os.environ.get("LIVEKIT_API_KEY") or read("/etc/appliance/livekit_key")
    secret = os.environ.get("LIVEKIT_API_SECRET") or read("/etc/appliance/livekit_secret")
    return key, secret
