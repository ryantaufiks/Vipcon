import hashlib
import hmac
import json
import os
import time

import requests

ETC = "/etc/appliance"


def read(path):
    with open(path) as f:
        return f.read().strip()


DEVICE_ID = os.environ.get("DEVICE_ID") or read(f"{ETC}/device_id")
DEVICE_SECRET = os.environ.get("DEVICE_SECRET") or read(f"{ETC}/device_secret")
CONTROL_PLANE_URL = os.environ.get("CONTROL_PLANE_URL", "https://control.domainutama.com")
INTERVAL = int(os.environ.get("HEARTBEAT_INTERVAL", "30"))
APP_VERSION = os.environ.get("APP_VERSION", "0.1.0")


def lan_ip():
    return os.environ.get("LAN_IP", "")


def sign(body):
    return hmac.new(DEVICE_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()


def heartbeat():
    body = json.dumps({
        "device_id": DEVICE_ID,
        "ts": int(time.time()),
        "app_version": APP_VERSION,
        "cpu_temp": None,
        "disk_pct": None,
        "mem_pct": None,
        "lan_ip": lan_ip(),
        "tunnel_ok": True,
        "meetings_count_24h": 0,
    })
    headers = {
        "X-Device-ID": DEVICE_ID,
        "X-Signature": sign(body),
        "Content-Type": "application/json",
    }
    try:
        r = requests.post(f"{CONTROL_PLANE_URL}/api/v1/heartbeat", data=body, headers=headers, timeout=10)
        print("heartbeat", r.status_code)
    except Exception as e:
        print("heartbeat error", e)


if __name__ == "__main__":
    while True:
        heartbeat()
        time.sleep(INTERVAL)
