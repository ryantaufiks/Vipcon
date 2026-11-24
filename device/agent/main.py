"""Device agent: signed heartbeat (metadata only) and license refresh.

Every request to the control plane is signed with the device secret:
    X-Device-ID, X-Timestamp (unix seconds),
    X-Signature = hex(HMAC-SHA256(secret, timestamp + "\\n" + METHOD + "\\n" + path + "\\n" + body))
"""

import hashlib
import hmac
import json
import os
import secrets
import shutil
import socket
import subprocess
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
TLS_DIR = os.environ.get("TLS_DIR", f"{ETC}/tls")
SITE_FILE = os.environ.get("SITE_FILE", f"{ETC}/site_fqdn")
CADDY_SITE = os.environ.get("CADDY_SITE", f"{ETC}/caddy-site.caddy")
CADDYFILE = os.environ.get("CADDYFILE", f"{ETC}/Caddyfile")
CADDY_ADMIN = os.environ.get("CADDY_ADMIN", "http://127.0.0.1:2019")
# Internet access: cloudflared runs the device's tunnel with this token; PUBLIC_FILE holds the
# public name once the tunnel is connected (links and QR codes use it, see device/app, kiosk).
TUNNEL_TOKEN_FILE = os.environ.get("TUNNEL_TOKEN_FILE", f"{ETC}/tunnel_token")
PUBLIC_FILE = os.environ.get("PUBLIC_FILE", f"{ETC}/public_fqdn")
CLOUDFLARED = os.environ.get("CLOUDFLARED", "/usr/local/bin/cloudflared")
CLOUDFLARED_METRICS = os.environ.get("CLOUDFLARED_METRICS", "127.0.0.1:20241")
# Seed for the key in the room-screen QR (see room_key in device/app and the kiosk): over the
# internet, the list of meetings in progress opens only for people who can see the screen.
ROOM_SEED_FILE = os.environ.get("ROOM_SEED_FILE", f"{ETC}/room_seed")


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
        "tunnel_ok": tunnel.ok,
        "cert_expires_at": cert_expires_at(),
        "meetings_count_24h": None,
    }
    try:
        r = post("/api/v1/heartbeat", payload)
        print("heartbeat", r.status_code, r.text[:200], flush=True)
    except requests.RequestException as e:
        print("heartbeat error", e, flush=True)
        check_tunnel()  # no answer: the site may have lost its internet connection
        return
    if r.status_code == 200:
        update_kiosk(r.json())
        update_site(r.json())
        update_tunnel(r.json())
    elif r.status_code == 403:
        update_kiosk({"license_status": "revoked", "claimed": True, "logo_sha256": None})
        update_tunnel({})  # revoked: no internet access
    else:
        check_tunnel()


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


def read_text(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def reload_caddy():
    """Ask Caddy (host network, admin API on localhost) to reload; it re-reads the cert files."""
    try:
        with open(CADDYFILE, "rb") as f:
            body = f.read()
        # must-revalidate: reload even when the Caddyfile is unchanged, so renewed certificate
        # files are picked up (Caddy otherwise skips identical configs and keeps the old cert).
        r = requests.post(CADDY_ADMIN + "/load", data=body, timeout=20,
                          headers={"Content-Type": "text/caddyfile", "Cache-Control": "must-revalidate"})
        print("caddy reload", r.status_code, r.text[:200], flush=True)
        return r.status_code == 200
    except (OSError, requests.RequestException) as e:
        print("caddy reload error", e, flush=True)
        return False


def update_site(info):
    """Office-network name: keep its certificate and Caddy site block in step with the control plane."""
    fqdn = info.get("site_fqdn") or ""
    want_sha = info.get("tls_sha256")

    def publish_name(value):
        # SITE_FILE is what the device app and the kiosk put in links and QR codes, so it only
        # names the device once Caddy serves that name with its certificate; until then they
        # fall back to the LAN IP.
        if value != read_text(SITE_FILE):
            write_atomic(SITE_FILE, value.encode())

    try:
        if not fqdn or not want_sha:
            publish_name("")
            if os.path.exists(CADDY_SITE):  # name removed (or no certificate): stop serving it
                os.remove(CADDY_SITE)
                reload_caddy()
            return
        chain_path = os.path.join(TLS_DIR, "fullchain.pem")
        site_block = (f"{fqdn} {{\n\ttls {chain_path} {os.path.join(TLS_DIR, 'privkey.pem')}\n"
                      f"\timport vipcon_routes\n}}\n")
        changed = False
        if file_sha256(chain_path) != want_sha:
            r = get("/api/v1/tls")
            if r.status_code != 200:
                print("tls fetch", r.status_code, flush=True)
                return
            tls = r.json()
            if hashlib.sha256(tls["fullchain"].encode()).hexdigest() != want_sha or tls.get("fqdn") != fqdn:
                print("tls fetch: mismatch, ignored", flush=True)
                return
            os.makedirs(TLS_DIR, mode=0o755, exist_ok=True)
            write_atomic(os.path.join(TLS_DIR, "privkey.pem"), tls["privkey"].encode(), 0o600)
            write_atomic(chain_path, tls["fullchain"].encode())
            print("tls certificate updated for", fqdn, "until", tls.get("not_after"), flush=True)
            changed = True
        if read_text(CADDY_SITE) != site_block.strip():
            write_atomic(CADDY_SITE, site_block.encode())
            changed = True
        # Also reload when the name isn't published yet: an earlier reload may have failed.
        if (changed or read_text(SITE_FILE) != fqdn) and not reload_caddy():
            return  # retried next heartbeat; links keep their previous name meanwhile
        publish_name(fqdn)
    except (OSError, ValueError, KeyError, requests.RequestException) as e:
        print("site update error", e, flush=True)


class Tunnel:
    """cloudflared, supervised by the agent. ok is None without internet access, else whether the
    tunnel is connected to Cloudflare."""

    def __init__(self):
        self.proc = None
        self.ok = None

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, token):
        self.stop()
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "TUNNEL_TOKEN": token}  # not on argv
        self.proc = subprocess.Popen([CLOUDFLARED, "tunnel", "--no-autoupdate", "--metrics", CLOUDFLARED_METRICS,
                                      "run"], env=env)
        print("cloudflared started", flush=True)

    def stop(self):
        if self.running():
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            print("cloudflared stopped", flush=True)
        self.proc = None

    def ready(self):
        try:
            return requests.get(f"http://{CLOUDFLARED_METRICS}/ready", timeout=3).status_code == 200
        except requests.RequestException:
            return False


tunnel = Tunnel()
public_fqdn = ""  # the public name the control plane last assigned, published while the tunnel is up


def publish_public(value):
    """Links and QR codes use PUBLIC_FILE when it holds a name, else the office-network link."""
    if value != read_text(PUBLIC_FILE):
        write_atomic(PUBLIC_FILE, value.encode())


def check_tunnel():
    """Without an answer from the control plane, keep the public name only while cloudflared is
    actually connected; otherwise fall back to the office-network link so meetings in the room
    keep working offline."""
    try:
        tunnel.ok = tunnel.ready() if tunnel.running() else None
        publish_public(public_fqdn if tunnel.ok and public_fqdn else "")
    except OSError as e:
        print("tunnel check error", e, flush=True)


def update_tunnel(info):
    """Internet access: run the tunnel the control plane assigned, and publish the public name only
    while the tunnel is connected (otherwise links fall back to the office-network name)."""
    global public_fqdn
    fqdn = info.get("public_fqdn") or ""
    want_sha = info.get("tunnel_sha256")
    publish = publish_public

    try:
        if not fqdn or not want_sha:
            public_fqdn = ""
            tunnel.stop()
            tunnel.ok = None
            publish("")
            if os.path.exists(TUNNEL_TOKEN_FILE):
                os.remove(TUNNEL_TOKEN_FILE)
            return
        token = read_text(TUNNEL_TOKEN_FILE)
        if hashlib.sha256(token.encode()).hexdigest() != want_sha:
            r = get("/api/v1/tunnel")
            if r.status_code != 200:
                print("tunnel fetch", r.status_code, flush=True)
                check_tunnel()
                return
            data = r.json()
            if hashlib.sha256(data["token"].encode()).hexdigest() != want_sha or data.get("public_fqdn") != fqdn:
                print("tunnel fetch: mismatch, ignored", flush=True)
                check_tunnel()
                return
            token = data["token"]
            write_atomic(TUNNEL_TOKEN_FILE, token.encode(), 0o600)
            tunnel.start(token)
        elif not tunnel.running():
            tunnel.start(token)
        public_fqdn = fqdn
        tunnel.ok = tunnel.ready()
        publish(fqdn if tunnel.ok else "")
    except (OSError, ValueError, KeyError, requests.RequestException) as e:
        print("tunnel update error", e, flush=True)


def ensure_room_seed():
    if not read_text(ROOM_SEED_FILE):
        write_atomic(ROOM_SEED_FILE, secrets.token_hex(32).encode(), 0o644)


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
    ensure_room_seed()
    publish_public("")  # cloudflared isn't running yet: links use the office-network name until it connects
    next_license = 0
    while True:
        heartbeat()
        if time.time() >= next_license:
            # Retry soon after a failure, otherwise refresh every LICENSE_INTERVAL.
            next_license = time.time() + (LICENSE_INTERVAL if refresh_license() else 300)
        time.sleep(INTERVAL)
