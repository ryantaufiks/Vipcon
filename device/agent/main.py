"""Device agent: signed heartbeat (metadata only) and license refresh.

Every request to the control plane is signed with the device secret:
    X-Device-ID, X-Timestamp (unix seconds),
    X-Signature = hex(HMAC-SHA256(secret, timestamp + "\\n" + METHOD + "\\n" + path + "\\n" + body))
"""

import hashlib
import hmac
import json
import os
import shutil
import socket
import time

import requests

ETC = "/etc/appliance"


def read(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


DEVICE_ID = os.environ.get("DEVICE_ID") or read(f"{ETC}/device_id")
DEVICE_SECRET = os.environ.get("DEVICE_SECRET") or read(f"{ETC}/device_secret")
CONTROL_PLANE_URL = (os.environ.get("CONTROL_PLANE_URL") or "https://control.domainutama.com").rstrip("/")
INTERVAL = int(os.environ.get("HEARTBEAT_INTERVAL", "30"))
LICENSE_INTERVAL = int(os.environ.get("LICENSE_INTERVAL", "21600"))
APP_VERSION = os.environ.get("APP_VERSION", "0.1.0")
LICENSE_FILE = os.environ.get("LICENSE_FILE", f"{ETC}/license.jwt")
CERT_FILE = os.environ.get("CERT_FILE", f"{ETC}/ca/server.pem")
KIOSK_DIR = os.environ.get("KIOSK_DIR", f"{ETC}/kiosk")


def sign(method, path, body):
    ts = str(int(time.time()))
    message = "\n".join([ts, method, path]).encode() + b"\n" + body
    signature = hmac.new(DEVICE_SECRET.encode(), message, hashlib.sha256).hexdigest()
    return {"X-Device-ID": DEVICE_ID, "X-Timestamp": ts, "X-Signature": signature}


def get(path):
    return requests.get(CONTROL_PLANE_URL + path, headers=sign("GET", path, b""), timeout=20)


def post(path, payload):
    body = json.dumps(payload).encode()
    headers = sign("POST", path, body)
    headers["Content-Type"] = "application/json"
    return requests.post(CONTROL_PLANE_URL + path, data=body, headers=headers, timeout=10)


def lan_ip():
    if os.environ.get("LAN_IP"):
        return os.environ["LAN_IP"]
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # no packet is sent; picks the LAN-facing address
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return ""


def cpu_temp():
    raw = read("/sys/class/thermal/thermal_zone0/temp")
    return round(int(raw) / 1000, 1) if raw.isdigit() else None


def disk_pct():
    usage = shutil.disk_usage("/")
    return round(usage.used * 100 / usage.total, 1)


def mem_pct():
    info = {}
    for line in read("/proc/meminfo").splitlines():
        key, _, value = line.partition(":")
        info[key] = int(value.split()[0]) if value.split() else 0
    total, available = info.get("MemTotal"), info.get("MemAvailable")
    return round((total - available) * 100 / total, 1) if total and available is not None else None


def uptime():
    raw = read("/proc/uptime").split()
    return int(float(raw[0])) if raw else None


def cert_expires_at():
    try:
        from cryptography import x509

        with open(CERT_FILE, "rb") as f:
            return x509.load_pem_x509_certificate(f.read()).not_valid_after_utc.isoformat()
    except Exception:
        return None


def heartbeat():
    # Metadata only. Never add user names, room names, participants or chat here.
    payload = {
        "device_id": DEVICE_ID,
        "app_version": APP_VERSION,
        "cpu_temp": cpu_temp(),
        "disk_pct": disk_pct(),
        "mem_pct": mem_pct(),
        "uptime": uptime(),
        "lan_ip": lan_ip(),
        "tunnel_ok": None,
        "cert_expires_at": cert_expires_at(),
        "meetings_count_24h": None,
    }
    try:
        r = post("/api/v1/heartbeat", payload)
        print("heartbeat", r.status_code, r.text[:200], flush=True)
    except requests.RequestException as e:
        print("heartbeat error", e, flush=True)
        return
    if r.status_code == 200:
        update_kiosk(r.json())
    elif r.status_code == 403:
        update_kiosk({"license_status": "revoked", "claimed": True, "logo_sha256": None})


def write_atomic(path, data, mode=0o644):
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def file_sha256(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def update_kiosk(info):
    """Keep the LCD kiosk's view of the control plane in /etc/appliance/kiosk.

    The kiosk never talks to the network; it only reads these two files.
    """
    try:
        os.makedirs(KIOSK_DIR, exist_ok=True)
        logo_path = os.path.join(KIOSK_DIR, "logo.png")
        want = info.get("logo_sha256")
        if not want:
            if os.path.exists(logo_path):
                os.remove(logo_path)
        elif file_sha256(logo_path) != want:
            r = get("/api/v1/branding/logo")
            if r.status_code == 200 and hashlib.sha256(r.content).hexdigest() == want:
                write_atomic(logo_path, r.content)
                print("logo updated", want[:12], flush=True)
        state = {
            "license_status": info.get("license_status"),
            "claimed": bool(info.get("claimed")),
            "client_name": info.get("client_name"),
            "tagline": info.get("tagline"),
            "updated_at": int(time.time()),
        }
        write_atomic(os.path.join(KIOSK_DIR, "state.json"), json.dumps(state).encode())
    except (OSError, ValueError, requests.RequestException) as e:
        print("kiosk state error", e, flush=True)


def refresh_license():
    """Store the signed license; the device keeps working offline until its exp."""
    try:
        r = post("/api/v1/license", {})
    except requests.RequestException as e:
        print("license error", e, flush=True)
        return False
    if r.status_code != 200:
        print("license refused", r.status_code, r.text[:200], flush=True)
        return False
    token = r.json()["license_token"]
    tmp = LICENSE_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write(token)
    os.chmod(tmp, 0o600)
    os.replace(tmp, LICENSE_FILE)
    print("license refreshed", r.json().get("status"), flush=True)
    return True


if __name__ == "__main__":
    next_license = 0
    while True:
        heartbeat()
        if time.time() >= next_license:
            # Retry soon after a failure, otherwise refresh every LICENSE_INTERVAL.
            next_license = time.time() + (LICENSE_INTERVAL if refresh_license() else 300)
        time.sleep(INTERVAL)
