"""Vipcon network setup: when the appliance has no network, let an admin connect it to Wi-Fi
from a phone.

    no default route for GRACE seconds (no Ethernet, Wi-Fi unknown or gone)
      -> start a WPA2 hotspot "Vipcon-Setup-XXXX" with a random password
      -> the LCD shows a Wi-Fi QR code for it (state file read by the kiosk)
      -> the phone joins; every DNS name points at the Pi, so the phone's captive-portal check
         opens the setup page served here on port 80
      -> the admin picks the office Wi-Fi and enters its password
      -> the hotspot goes down, the Pi joins that network (new NetworkManager profile)
      -> success: back to normal; failure: hotspot again, with the error on screen and page

Saved networks are retried every RETRY_KNOWN seconds while in setup, so a router that was only
rebooting brings the appliance straight back. Ethernet coming up ends setup immediately.
Only someone who can read the device's screen can get on the hotspot.

Runs as root (NetworkManager, port 80, stopping Caddy/dnsmasq while the hotspot is up).
"""

import html
import json
import os
import re
import secrets
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE_DIR = os.environ.get("NETSETUP_STATE_DIR", "/run/vipcon-netsetup")
STATE_FILE = os.path.join(STATE_DIR, "state.json")
# `touch` this to enter setup now, even when online (testing, or re-pointing a device on site).
FORCE_FILE = os.path.join(STATE_DIR, "force")
ETC = os.environ.get("APPLIANCE_ETC", "/etc/appliance")
HOTSPOT_CON = "vipcon-setup"
PROFILE_PREFIX = "vipcon-wifi-"
GRACE = int(os.environ.get("NETSETUP_GRACE", "90"))          # offline this long -> setup
CHECK = int(os.environ.get("NETSETUP_CHECK", "5"))
RETRY_KNOWN = int(os.environ.get("NETSETUP_RETRY_KNOWN", "120"))
CONNECT_WAIT = int(os.environ.get("NETSETUP_CONNECT_WAIT", "30"))
PORTAL_PORT = int(os.environ.get("NETSETUP_PORT", "80"))
CADDY_CONTAINER = os.environ.get("NETSETUP_CADDY", "appliance-caddy-1")
APPLY_LAN_IP = os.environ.get("NETSETUP_APPLY_LAN_IP", "/opt/appliance/apply-lan-ip.sh")
IGNORE_IFACES = ("tailscale", "docker", "veth", "br-", "lo")


def log(*args):
    print("netsetup:", *args, flush=True)


# ---------------------------------------------------------------- system access (patched in tests)


def run(args, timeout=30):
    """Run a command; return (exit code, stdout). Never raises."""
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def nmcli(*args, timeout=30):
    return run(["nmcli", *args], timeout=timeout)


def online():
    """True if the appliance has a default route over a real LAN interface."""
    code, out = run(["ip", "-4", "route", "show", "default"])
    for line in out.splitlines() if code == 0 else []:
        m = re.search(r"\bdev (\S+)", line)
        if m and not m.group(1).startswith(IGNORE_IFACES):
            return True
    return False


def lan_ip():
    code, out = run(["ip", "-4", "route", "get", "1.1.1.1"])
    m = re.search(r"\bsrc (\S+)", out) if code == 0 else None
    return m.group(1) if m else ""


# ---------------------------------------------------------------- NetworkManager helpers


def split_terse(line):
    """Split one line of `nmcli -t` output (':' separated, '\\:' and '\\\\' escaped)."""
    fields, cur, i = [], [], 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            cur.append(line[i + 1])
            i += 2
            continue
        if ch == ":":
            fields.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    fields.append("".join(cur))
    return fields


def wifi_iface():
    code, out = nmcli("-t", "-f", "DEVICE,TYPE", "device")
    for line in out.splitlines() if code == 0 else []:
        dev, typ = (split_terse(line) + ["", ""])[:2]
        if typ == "wifi":
            return dev
    return None


def scan(iface):
    """Nearby networks, strongest first: [{"ssid", "signal", "secure"}]."""
    code, out = nmcli("-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list",
                      "ifname", iface, "--rescan", "yes", timeout=40)
    best = {}
    for line in out.splitlines() if code == 0 else []:
        ssid, signal, security = (split_terse(line) + ["", "", ""])[:3]
        if not ssid:
            continue  # hidden network: can still be typed in by hand
        try:
            signal = int(signal)
        except ValueError:
            signal = 0
        secure = bool(security.strip()) and security.strip() != "--"
        if ssid not in best or signal > best[ssid]["signal"]:
            best[ssid] = {"ssid": ssid, "signal": signal, "secure": secure}
    return sorted(best.values(), key=lambda n: -n["signal"])


def known_wifi():
    code, out = nmcli("-t", "-f", "NAME,TYPE", "connection", "show")
    names = []
    for line in out.splitlines() if code == 0 else []:
        name, typ = (split_terse(line) + ["", ""])[:2]
        if typ == "802-11-wireless" and name != HOTSPOT_CON:
            names.append(name)
    return names


def start_hotspot(iface, ssid, password):
    nmcli("connection", "delete", HOTSPOT_CON)
    code, out = nmcli("device", "wifi", "hotspot", "ifname", iface, "con-name", HOTSPOT_CON,
                      "ssid", ssid, "password", password, timeout=40)
    if code != 0:
        log("hotspot failed:", out.strip()[:200])
    return code == 0


def stop_hotspot():
    nmcli("connection", "down", HOTSPOT_CON)
    nmcli("connection", "delete", HOTSPOT_CON)


def wait_online(seconds):
    end = time.time() + seconds
    while time.time() < end:
        if online():
            return True
        time.sleep(1)
    return online()


def try_known(names):
    for name in names:
        log("trying saved network", name)
        nmcli("connection", "up", name, timeout=25)
        if wait_online(8):
            return name
    return None


def connect(iface, ssid, password):
    """Create (or replace) our profile for this SSID and bring it up. Returns (ok, error)."""
    name = PROFILE_PREFIX + re.sub(r"[^A-Za-z0-9._-]", "_", ssid)[:40]
    nmcli("connection", "delete", name)
    args = ["connection", "add", "type", "wifi", "ifname", iface, "con-name", name, "ssid", ssid,
            "connection.autoconnect", "yes", "connection.autoconnect-priority", "10"]
    if password:
        args += ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password]
    code, out = nmcli(*args)
    if code != 0:
        return False, "Could not save the network settings."
    code, out = nmcli("connection", "up", name, timeout=CONNECT_WAIT + 10)
    if code == 0 and wait_online(CONNECT_WAIT):
        return True, ""
    nmcli("connection", "delete", name)  # do not keep a profile that does not work
    low = out.lower()
    if "secrets were required" in low or "802-1x" in low or "password" in low:
        return False, f"Could not join {ssid}: the password looks wrong."
    if password:
        return False, f"Could not join {ssid}. Check the password and try again."
    return False, f"Could not join {ssid}. If it has a password, enter it and try again."


def appliance_services(start):
    """Caddy (port 80) and our dnsmasq (port 53) step aside while the hotspot runs."""
    verb = "start" if start else "stop"
    run(["systemctl", verb, "dnsmasq"])
    run(["docker", verb, CADDY_CONTAINER], timeout=60)


# ---------------------------------------------------------------- state shared with the kiosk


def write_state(**state):
    state["updated_at"] = int(time.time())
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.chmod(tmp, 0o600)  # holds the hotspot password
    os.replace(tmp, STATE_FILE)


def hotspot_ssid():
    try:
        device_id = open(os.path.join(ETC, "device_id")).read().strip()
    except OSError:
        device_id = ""
    tail = re.sub(r"[^a-z0-9]", "", device_id.lower())[-4:] or secrets.token_hex(2)
    return "Vipcon-Setup-" + tail.upper()


def hotspot_password():
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i: it is read off a small screen
    return "".join(secrets.choice(alphabet) for _ in range(10))


# ---------------------------------------------------------------- captive portal

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Vipcon Wi-Fi setup</title>
<style>
:root{{color-scheme:dark}}*{{box-sizing:border-box}}body{{margin:0;font:16px/1.45 system-ui,sans-serif;background:#07080c;color:#eef0f4}}
main{{max-width:440px;margin:0 auto;padding:28px 20px}}h1{{font-size:22px;margin:0 0 4px}}p{{color:#a3aab6;margin:0 0 18px}}
.chip{{display:inline-block;font-size:11px;letter-spacing:.12em;text-transform:uppercase;border:1px solid #7a1d1d;
background:#1d0b0b;border-radius:99px;padding:3px 10px;margin-bottom:14px;color:#f3c9c9}}
.net{{display:flex;align-items:center;gap:10px;padding:12px 14px;border:1px solid #23262f;border-radius:10px;margin-bottom:8px;background:#0e1016}}
.net input{{accent-color:#ff2a2a}}.net span{{flex:1}}.sig{{color:#7f8794;font-size:13px}}
label.f{{display:block;font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#a3aab6;margin:16px 0 6px}}
input[type=text],input[type=password]{{width:100%;padding:12px;border-radius:10px;border:1px solid #2b2f3a;background:#0e1016;color:#fff;font-size:16px}}
button{{width:100%;margin-top:20px;padding:14px;border:0;border-radius:10px;background:#ff1f1f;color:#fff;font-size:16px;font-weight:600}}
.err{{background:#2a1010;border:1px solid #7a1d1d;color:#ffd5d5;padding:10px 12px;border-radius:10px;margin-bottom:16px}}
.muted{{color:#7f8794;font-size:13px}}
</style></head><body><main>{body}</main></body></html>"""


def network_list_html(networks):
    if not networks:
        return '<p class="muted">No networks found nearby. Type the network name below.</p>'
    rows = []
    for i, n in enumerate(networks[:15]):
        ssid = html.escape(n["ssid"])
        lock = " 🔒" if n["secure"] else ""
        rows.append(f'<label class="net"><input type="radio" name="ssid" value="{ssid}"{" checked" if i == 0 else ""}>'
                    f'<span>{ssid}{lock}</span><span class="sig">{n["signal"]}%</span></label>')
    return "".join(rows)


def setup_page(networks, error=""):
    err = f'<div class="err">{html.escape(error)}</div>' if error else ""
    body = (f'<div class="chip">Wi-Fi setup</div><h1>Connect Vipcon to Wi-Fi</h1>'
            f'<p>Choose the office network this device should use.</p>{err}'
            f'<form method="post" action="/connect">{network_list_html(networks)}'
            f'<label class="f" for="other">Other network (name)</label>'
            f'<input type="text" id="other" name="other" maxlength="32" autocomplete="off" autocapitalize="none" placeholder="Leave empty to use the selection">'
            f'<label class="f" for="pw">Wi-Fi password</label>'
            f'<input type="password" id="pw" name="password" maxlength="63" autocomplete="off" placeholder="Leave empty for an open network">'
            f'<button>Connect</button></form>'
            f'<p class="muted" style="margin-top:16px">This page is served by the Vipcon device itself. '
            f'Your password is only stored on the device.</p>')
    return PAGE.format(body=body)


def connecting_page(ssid):
    body = (f'<div class="chip">Connecting</div><h1>Connecting to {html.escape(ssid)}…</h1>'
            '<p>This setup network will now disappear. Reconnect your phone to the office Wi-Fi and '
            'follow the device screen: it shows when Vipcon is online, or asks you to try again.</p>')
    return PAGE.format(body=body)


class Portal:
    """HTTP server for the setup page; every path serves it (captive-portal detection)."""

    def __init__(self, port=PORTAL_PORT):
        self.networks, self.error, self.request = [], "", None
        self.lock = threading.Lock()
        portal = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def send_page(self, text, status=200):
                data = text.encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                with portal.lock:
                    self.send_page(setup_page(portal.networks, portal.error))

            def do_POST(self):
                length = min(int(self.headers.get("Content-Length") or 0), 4096)
                form = urllib.parse.parse_qs(self.rfile.read(length).decode(errors="replace"))
                ssid = (form.get("other", [""])[0].strip() or form.get("ssid", [""])[0]).strip()
                password = form.get("password", [""])[0]
                with portal.lock:
                    secure = next((n["secure"] for n in portal.networks if n["ssid"] == ssid), None)
                error = validate(ssid, password, secure)
                with portal.lock:
                    if error:
                        portal.error = error
                        self.send_page(setup_page(portal.networks, error), 400)
                        return
                    portal.request = (ssid, password)
                self.send_page(connecting_page(ssid))

        self.server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def take_request(self):
        with self.lock:
            req, self.request = self.request, None
            return req

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def validate(ssid, password, secure=None):
    """secure: True/False if the network was seen in the scan, None if typed in by hand."""
    if not ssid or len(ssid.encode()) > 32:
        return "Choose a network or type its name (up to 32 characters)."
    if secure and not password:
        return f"{ssid} needs a password."
    if password and not 8 <= len(password) <= 63:
        return "Wi-Fi passwords are 8 to 63 characters."
    return ""


# ---------------------------------------------------------------- main loop


class NetSetup:
    def __init__(self):
        self.iface = wifi_iface()
        self.offline_since = None
        self.portal = None
        self.ap_ssid, self.ap_password = hotspot_ssid(), hotspot_password()

    def tick(self):
        """One step of the normal (not in setup) loop. Returns True if setup was entered."""
        if online():
            self.offline_since = None
            return False
        self.offline_since = self.offline_since or time.time()
        if time.time() - self.offline_since >= GRACE:
            self.setup()
            return True
        return False

    def open_hotspot(self, networks, error=""):
        self.portal.networks, self.portal.error = networks, error
        start_hotspot(self.iface, self.ap_ssid, self.ap_password)
        write_state(mode="failed" if error else "setup", ap_ssid=self.ap_ssid,
                    ap_password=self.ap_password, error=error)

    def setup(self):
        if not self.iface:
            log("no Wi-Fi interface; cannot offer setup")
            write_state(mode="no-wifi")
            return
        log("offline: starting Wi-Fi setup as", self.ap_ssid)
        networks = scan(self.iface)  # before the hotspot: one radio cannot scan while serving
        appliance_services(start=False)
        try:
            self.portal = Portal()
            self.open_hotspot(networks)
            next_retry = time.time() + RETRY_KNOWN
            while True:
                time.sleep(1)
                req = self.portal.take_request()
                if req:
                    ssid, password = req
                    write_state(mode="connecting", target_ssid=ssid)
                    time.sleep(2)  # let the "Connecting…" page reach the phone
                    stop_hotspot()
                    ok, error = connect(self.iface, ssid, password)
                    if ok:
                        self.finish(ssid)
                        return
                    log("connect failed:", error)
                    self.open_hotspot(scan(self.iface), error)
                    next_retry = time.time() + RETRY_KNOWN
                elif online():  # e.g. an Ethernet cable was plugged in
                    self.finish(None)
                    return
                elif time.time() >= next_retry and known_wifi():
                    stop_hotspot()
                    name = try_known(known_wifi())
                    if name:
                        self.finish(name)
                        return
                    self.open_hotspot(scan(self.iface), self.portal.error)
                    next_retry = time.time() + RETRY_KNOWN
        finally:
            if self.portal:
                self.portal.close()
                self.portal = None
            stop_hotspot()
            appliance_services(start=True)

    def finish(self, network):
        ip = lan_ip()
        log("online via", network or "existing link", ip)
        if os.path.exists(APPLY_LAN_IP):
            run([APPLY_LAN_IP], timeout=180)  # new network, maybe a new LAN IP: certs, LiveKit, DNS
        write_state(mode="connected", network=network or "", ip=ip)
        self.offline_since = None
        self.ap_password = hotspot_password()  # never reuse a password that was on screen


def main():
    ns = NetSetup()
    write_state(mode="online" if online() else "offline")
    while True:
        try:
            if os.path.exists(FORCE_FILE):
                os.remove(FORCE_FILE)
                log("setup requested via", FORCE_FILE)
                ns.setup()
            else:
                ns.tick()
        except Exception as e:  # keep the appliance recoverable whatever happens
            log("error:", e)
        time.sleep(CHECK)


if __name__ == "__main__":
    main()
