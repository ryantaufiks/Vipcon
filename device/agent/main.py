"""Device agent: signed heartbeat (metadata only) and license refresh.

Every request to the control plane is signed with the device secret:
    X-Device-ID, X-Timestamp (unix seconds),
    X-Signature = hex(HMAC-SHA256(secret, timestamp + "\\n" + METHOD + "\\n" + path + "\\n" + body))
"""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import socket
import struct
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
# Internet access, media: guests outside the office reach LiveKit through our TURN relay. For that
# LiveKit advertises the site's public IPv4 (as well as its LAN addresses) and hands browsers the
# relay. The agent edits livekit.yaml, then asks the host to restart LiveKit by touching
# LIVEKIT_RESTART (vipcon-livekit-restart.path), only while no meeting has anyone in it.
LIVEKIT_YAML = os.environ.get("LIVEKIT_YAML", f"{ETC}/livekit.yaml")
LIVEKIT_RESTART = os.environ.get("LIVEKIT_RESTART", f"{ETC}/livekit.restart")
LIVEKIT_URL = os.environ.get("LIVEKIT_URL", "http://127.0.0.1:7880")
LIVEKIT_KEY_FILE = os.environ.get("LIVEKIT_KEY_FILE", f"{ETC}/livekit_key")
LIVEKIT_SECRET_FILE = os.environ.get("LIVEKIT_SECRET_FILE", f"{ETC}/livekit_secret")
LAN_IP_FILE = os.environ.get("LAN_IP_FILE", f"{ETC}/lan_ip")
STUN_SERVERS = [("stun.cloudflare.com", 3478), ("stun.l.google.com", 19302)]
PUBLIC_IP_INTERVAL = int(os.environ.get("PUBLIC_IP_INTERVAL", "600"))


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
        update_media(r.json())
    elif r.status_code == 403:
        update_kiosk({"license_status": "revoked", "claimed": True, "logo_sha256": None})
        update_tunnel({})  # revoked: no internet access
        update_media({})
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


# ---------------------------------------------------------------- media relay (TURN)

MEDIA_BEGIN = "  # >>> internet access: managed by the agent"
MEDIA_END = "  # <<< internet access"
media = {"turn": None, "turn_sha": None, "public_ip": "", "public_ip_at": 0, "waiting": False}


def stun_public_ipv4(servers=None, timeout=3):
    """The site's public IPv4 as the internet sees it (STUN binding request, RFC 5389), or ""."""
    for host, port in servers or STUN_SERVERS:
        tid = secrets.token_bytes(12)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(timeout)
                s.sendto(struct.pack("!HHI", 0x0001, 0, 0x2112A442) + tid, (host, port))
                ip = parse_stun_response(s.recvfrom(2048)[0], tid)
        except OSError:
            continue
        if ip:
            return ip
    return ""


def parse_stun_response(data, tid):
    """The IPv4 in a STUN binding success response's (XOR-)MAPPED-ADDRESS, or ""."""
    if len(data) < 20 or struct.unpack("!H", data[:2])[0] != 0x0101 or data[8:20] != tid:
        return ""
    pos = 20
    while pos + 4 <= len(data):
        kind, size = struct.unpack("!HH", data[pos:pos + 4])
        value = data[pos + 4:pos + 4 + size]
        if kind in (0x0020, 0x0001) and len(value) >= 8 and value[1] == 0x01:
            raw = struct.unpack("!I", value[4:8])[0]
            if kind == 0x0020:
                raw ^= 0x2112A442
            return socket.inet_ntoa(struct.pack("!I", raw))
        pos += 4 + size + (-size % 4)
    return ""


def valid_turn(turn):
    """Only plain values go into livekit.yaml."""
    try:
        return bool(turn["servers"]) and re.fullmatch(r"[A-Za-z0-9_-]{16,128}", turn["secret"]) is not None and all(
            re.fullmatch(r"[A-Za-z0-9.-]{1,253}", srv["host"]) and srv["protocol"] in ("udp", "tcp", "tls")
            and 0 < int(srv["port"]) < 65536 for srv in turn["servers"]) and 0 < int(turn.get("ttl") or 14400)
    except (KeyError, TypeError, ValueError):
        return False


def render_livekit(text, node_ip, turn):
    """livekit.yaml with node_ip set to node_ip and, with turn, the relay block: LiveKit keeps
    advertising its LAN addresses (advertise_internal_ip) next to node_ip, and gives browsers
    credentials for the relay. Without turn the block is removed."""
    out, skip = [], False
    for line in text.splitlines():
        if line.strip() == MEDIA_BEGIN.strip():
            skip = True
            continue
        if skip:
            skip = line.strip() != MEDIA_END.strip()
            continue
        if node_ip and re.match(r"^\s+node_ip:", line):
            line = line[:line.index("node_ip:")] + f"node_ip: {node_ip}"
        out.append(line)
        if turn and line.rstrip() == "rtc:":
            out += [MEDIA_BEGIN, "  advertise_internal_ip: true", "  turn_servers:"]
            for srv in turn["servers"]:
                out += [f"    - host: {srv['host']}", f"      port: {int(srv['port'])}",
                        f"      protocol: {srv['protocol']}", f"      secret: {turn['secret']}",
                        f"      ttl: {int(turn.get('ttl') or 14400)}"]
            out.append(MEDIA_END)
    return "\n".join(out) + "\n"


def jwt_hs256(claims, secret):
    def b64(data):
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()) + "." + b64(json.dumps(claims).encode())
    return head + "." + b64(hmac.new(secret.encode(), head.encode(), hashlib.sha256).digest())


def livekit_busy():
    """True while a meeting has someone in it, or when LiveKit can't tell (restarting it then would
    drop people). False when LiveKit isn't running at all."""
    token = jwt_hs256({"iss": read_text(LIVEKIT_KEY_FILE), "nbf": int(time.time()) - 10,
                       "exp": int(time.time()) + 60, "video": {"roomList": True}}, read_text(LIVEKIT_SECRET_FILE))
    try:
        r = requests.post(f"{LIVEKIT_URL}/twirp/livekit.RoomService/ListRooms", json={},
                          headers={"Authorization": f"Bearer {token}"}, timeout=5)
    except requests.ConnectionError:
        return False
    except requests.RequestException:
        return True
    if r.status_code != 200:
        print("livekit rooms", r.status_code, r.text[:200], flush=True)
        return True
    try:
        rooms = r.json().get("rooms") or []
        return any(int(room.get("num_participants") or room.get("numParticipants") or 0) > 0 for room in rooms)
    except (ValueError, TypeError, AttributeError):
        return True


def update_media(info):
    """Called with the control plane's answer only: offline, livekit.yaml is left as it is (a stale
    public address is harmless, and LAN meetings keep working either way)."""
    want = info.get("turn_sha256")
    try:
        if not want:
            media["turn"], media["turn_sha"] = None, None
        elif media["turn_sha"] != want:
            r = get("/api/v1/turn")
            if r.status_code != 200:
                print("turn fetch", r.status_code, flush=True)
                return
            turn = r.json()
            sha = hashlib.sha256(json.dumps(turn, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            if sha != want or not valid_turn(turn):
                print("turn fetch: mismatch or invalid, ignored", flush=True)
                return
            media["turn"], media["turn_sha"] = turn, want
        turn = media["turn"]
        if turn and time.time() - media["public_ip_at"] >= PUBLIC_IP_INTERVAL:
            media["public_ip"] = stun_public_ipv4() or media["public_ip"]
            media["public_ip_at"] = time.time()
        if turn and not media["public_ip"]:
            return  # no public address yet: try again later
        current = read_text(LIVEKIT_YAML)
        if not turn and MEDIA_BEGIN.strip() not in current:
            return  # never set up here: leave the installer's config alone
        node_ip = media["public_ip"] if turn else (read_text(LAN_IP_FILE) or lan_ip())
        apply_livekit(render_livekit(current + "\n", node_ip, turn))
    except (OSError, ValueError, KeyError, requests.RequestException) as e:
        print("media update error", e, flush=True)


def apply_livekit(text):
    """Write livekit.yaml and have the host restart LiveKit, once no meeting is running."""
    with open(LIVEKIT_YAML) as f:
        current = f.read()
    if text.strip() == current.strip():
        media["waiting"] = False
        return
    if livekit_busy():
        if not media["waiting"]:
            print("livekit config changed: waiting until no meeting is running", flush=True)
        media["waiting"] = True
        return
    # In place (same inode): LiveKit's container bind-mounts this single file.
    with open(LIVEKIT_YAML, "w") as f:
        f.write(text)
    os.chmod(LIVEKIT_YAML, 0o600)  # holds the LiveKit API secret and the relay secret
    write_atomic(LIVEKIT_RESTART, str(int(time.time())).encode())
    media["waiting"] = False
    print("livekit config updated, restart requested", flush=True)


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
