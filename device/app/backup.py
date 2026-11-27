"""Encrypted off-site backup of the device's database (accounts, meetings, settings).

Opt-in. When an admin turns it on, the device makes a Backup Key: a random 128-bit locator plus a
random 256-bit AES key, shown to the admin once as one string (VBK1-…). Every backup is a
consistent SQLite snapshot, gzipped and sealed with AES-256-GCM (the locator is the associated
data) on the device, then uploaded to the control plane, which keeps only the ciphertext. Only the
client holds the key: neither the control plane nor VIPCON can read a backup.

Restore (same or replacement device): the admin enters the Backup Key; the device downloads the
newest copy that opens with it, checks it is a VIPCON database, and swaps it in.

    sealed = b"VBK1" + nonce(12) + AESGCM(key).encrypt(nonce, gzip(db), aad=locator)
"""

import base64
import gzip
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import tempfile
import time

import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"VBK1"
STATE_FILE = os.environ.get("BACKUP_STATE_FILE", "/data/backup.json")  # key + status; never backed up
DEVICE_SECRET_FILE = os.environ.get("DEVICE_SECRET_FILE", "/etc/appliance/device_secret")
INTERVAL = 24 * 3600


class BackupError(Exception):
    """A message safe to show the admin."""


# ---------------------------------------------------------------- the Backup Key


def new_key():
    return secrets.token_hex(16), secrets.token_bytes(32)


def encode_key(locator, key):
    raw = base64.b32encode(bytes.fromhex(locator) + key).decode().rstrip("=")
    return "VBK1-" + "-".join(raw[i:i + 6] for i in range(0, len(raw), 6))


def decode_key(text):
    """(locator, key) from a Backup Key as typed or pasted; BackupError if it is not one."""
    s = re.sub(r"[^A-Z0-9]", "", (text or "").upper())
    if s.startswith("VBK1"):
        s = s[4:]
    try:
        raw = base64.b32decode(s + "=" * (-len(s) % 8))
    except ValueError:
        raise BackupError("That is not a valid Backup Key.")
    if len(raw) != 48:
        raise BackupError("That is not a valid Backup Key.")
    return raw[:16].hex(), raw[16:]


# ---------------------------------------------------------------- sealing


def seal(locator, key, data):
    nonce = secrets.token_bytes(12)
    return MAGIC + nonce + AESGCM(key).encrypt(nonce, gzip.compress(data), locator.encode())


def unseal(locator, key, blob):
    if not blob.startswith(MAGIC):
        raise BackupError("not a sealed backup")
    try:
        return gzip.decompress(AESGCM(key).decrypt(blob[4:16], blob[16:], locator.encode()))
    except Exception:
        raise BackupError("This Backup Key does not open the backup.")


def snapshot(db_path):
    """A consistent copy of the live SQLite database, as bytes."""
    src = sqlite3.connect(db_path)
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        dst = sqlite3.connect(tmp)
        src.backup(dst)
        dst.close()
        with open(tmp, "rb") as f:
            return f.read()
    finally:
        src.close()
        os.remove(tmp)


def check_database(path):
    """A restored file must be a VIPCON device database with at least one active admin."""
    try:
        con = sqlite3.connect(path)
        admins = con.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1").fetchone()[0]
        con.execute("SELECT id FROM meetings LIMIT 1")
        con.close()
    except sqlite3.Error:
        raise BackupError("The backup is not a VIPCON device database.")
    if not admins:
        raise BackupError("The backup has no Client Admin account.")


# ---------------------------------------------------------------- state (key and last result)


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


def forget_state():
    try:
        os.remove(STATE_FILE)
    except OSError:
        pass


def key_of(state):
    return state["locator"], base64.b64decode(state["key"])


def public_status(state):
    return {"enabled": bool(state.get("enabled")), "last_ok_at": state.get("last_ok_at"),
            "last_error": state.get("last_error"), "last_size": state.get("last_size"),
            "last_attempt_at": state.get("last_attempt_at")}


# ---------------------------------------------------------------- control plane


class Remote:
    """Signed calls to the control plane, as the device (same scheme as the agent)."""

    def __init__(self, base_url, device_id, secret_file=DEVICE_SECRET_FILE):
        self.base, self.device_id, self.secret_file = base_url.rstrip("/"), device_id, secret_file

    def _headers(self, method, path, body):
        try:
            with open(self.secret_file) as f:
                secret = f.read().strip()
        except OSError:
            raise BackupError("This device is not provisioned for backups.")
        ts = str(int(time.time()))
        msg = ts.encode() + b"\n" + method.encode() + b"\n" + path.encode() + b"\n" + body
        return {"X-Device-ID": self.device_id, "X-Timestamp": ts,
                "X-Signature": hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()}

    def _call(self, method, path, body=b""):
        if not self.base or not self.device_id:
            raise BackupError("This device has no connection to the VIPCON service, so it cannot back up off-site.")
        try:
            r = requests.request(method, self.base + path, data=body or None, timeout=60,
                                 headers=self._headers(method, path, body))
        except requests.RequestException:
            raise BackupError("Cannot reach the VIPCON service. Check the device's internet connection.")
        if r.status_code >= 400:
            raise BackupError(f"The VIPCON service refused the backup ({r.status_code}).")
        return r

    def upload(self, locator, blob):
        return self._call("POST", f"/api/v1/backup/{locator}", blob).json()

    def versions(self, locator):
        return self._call("GET", f"/api/v1/backup/{locator}").json().get("versions", [])

    def fetch(self, locator, name):
        return self._call("GET", f"/api/v1/backup/{locator}/{name}").content

    def delete(self, locator):
        return self._call("DELETE", f"/api/v1/backup/{locator}").json()


# ---------------------------------------------------------------- operations


def run(db_path, remote):
    """Back up now. Records the result in the state file; returns the public status."""
    state = load_state()
    if not state.get("enabled"):
        raise BackupError("Backups are off.")
    state["last_attempt_at"] = int(time.time())
    try:
        locator, key = key_of(state)
        blob = seal(locator, key, snapshot(db_path))
        remote.upload(locator, blob)
        state.update(last_ok_at=int(time.time()), last_error=None, last_size=len(blob))
    except BackupError as e:
        state["last_error"] = str(e)
    save_state(state)
    return public_status(state)


def due(now=None):
    state = load_state()
    return bool(state.get("enabled")) and (now or time.time()) - (state.get("last_attempt_at") or 0) >= INTERVAL


def download(remote, locator, key):
    """The newest copy that opens with the key, as a SQLite file path (the caller removes it)."""
    versions = remote.versions(locator)
    if not versions:
        raise BackupError("No backup was found for this Backup Key.")
    last = None
    for v in versions[:5]:
        try:
            data = unseal(locator, key, remote.fetch(locator, v["name"]))
        except BackupError as e:
            last = e
            continue
        fd, path = tempfile.mkstemp(suffix=".db", dir=os.path.dirname(STATE_FILE) or None)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        try:
            check_database(path)
        except BackupError:
            os.remove(path)
            raise
        return path
    raise last or BackupError("No backup could be opened with this Backup Key.")
