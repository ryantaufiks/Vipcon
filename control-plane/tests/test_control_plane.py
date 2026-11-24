"""End-to-end tests for the control plane, including the device agent's signing.

    cd control-plane && pip install -r app/requirements.txt pytest httpx requests && pytest -q tests
"""

import importlib.util
import json
import os
import re
import sys
import tempfile
import time
from datetime import date, timedelta
from html import unescape

import jwt
import pyotp
import pytest

TMP = tempfile.mkdtemp()
os.environ.update(
    SECRET_KEY="test-secret-key-0123456789abcdef0123456789",
    # Set TEST_DATABASE_URL to run against an empty, throwaway Postgres database.
    DATABASE_URL=os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{TMP}/cp.db",
    LICENSE_KEY_PATH=f"{TMP}/license.pem",
    COOKIE_SECURE="0",
    ROOT_DOMAIN="example.test",
    CONTROL_PLANE_URL="http://testserver",
    TLS_DIR=f"{TMP}/tls",
    CERTS_WANTED=f"{TMP}/certs/wanted.txt",
    CF_API_TOKEN="test-token",
    CF_ZONE_ID="test-zone",
)
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "app"))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from db import SessionLocal  # noqa: E402
from models import Base, SuperAdmin  # noqa: E402
from db import engine  # noqa: E402
from security import LICENSE_PUBLIC_PEM, hash_password, new_totp_secret  # noqa: E402

EMAIL = "root@example.test"
PASSWORD = "correct horse battery"


@pytest.fixture(scope="module")
def totp_secret():
    Base.metadata.create_all(engine)
    secret = new_totp_secret()
    db = SessionLocal()
    db.add(SuperAdmin(email=EMAIL, password_hash=hash_password(PASSWORD), totp_secret=secret))
    db.commit()
    db.close()
    return secret


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:
        yield c


def csrf_of(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


@pytest.fixture(scope="module")
def admin(client, totp_secret):
    code = pyotp.TOTP(totp_secret).now()
    r = client.post("/login", data={"email": EMAIL, "password": PASSWORD, "totp": code}, follow_redirects=False)
    assert r.status_code == 303, r.text
    page = client.get("/")
    assert page.status_code == 200
    return csrf_of(page.text)


def load_agent(device_id, secret):
    os.environ["DEVICE_ID"] = device_id
    os.environ["DEVICE_SECRET"] = secret
    os.environ["LICENSE_FILE"] = f"{TMP}/license.jwt"
    spec = importlib.util.spec_from_file_location(
        "agent_main", os.path.join(HERE, "..", "..", "device", "agent", "main.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def signed(client, agent, path, payload):
    body = json.dumps(payload).encode()
    headers = agent.sign("POST", path, body)
    headers["Content-Type"] = "application/json"
    return client.post(path, content=body, headers=headers)


def signed_get(client, agent, path):
    return client.get(path, headers=agent.sign("GET", path, b""))


def test_requires_login(client):
    r = client.get("/devices", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_login_rejects_bad_totp(client, totp_secret):
    r = client.post("/login", data={"email": EMAIL, "password": PASSWORD, "totp": "000000"})
    assert r.status_code == 401


def test_csrf_required(client, admin):
    r = client.post("/plans", data={"name": "x", "connectivity_mode": "standard"})
    assert r.status_code == 403


def test_full_device_lifecycle(client, admin):
    r = client.post(
        "/plans",
        data={"csrf": admin, "name": "Standard", "connectivity_mode": "standard", "price_monthly": "1,500",
              "max_participants": "10", "max_users": "20", "max_concurrent_meetings": "5", "active": "on"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    r = client.post("/clients", data={"csrf": admin, "name": "PT Contoh"}, follow_redirects=False)
    assert r.status_code == 303
    client_id = int(r.headers["location"].rsplit("/", 1)[1])

    r = client.post("/devices", data={"csrf": admin, "label": "Boardroom", "client_id": str(client_id), "plan_id": "1"})
    assert r.status_code == 200
    payload = json.loads(unescape(re.search(r"<pre>(.*?)</pre>", r.text, re.S).group(1)))
    device_id, secret, claim_code = payload["device_id"], payload["device_secret"], payload["claim_code"]
    assert payload["license_public_key"] == LICENSE_PUBLIC_PEM
    auto_name = device_id.replace("_", "-") + ".lan.example.test"  # no '_' in host names
    assert f"https://{auto_name}/claim?c={claim_code}" in unescape(r.text)

    agent = load_agent(device_id, secret)

    # No contract yet: license is issued but not active.
    r = signed(client, agent, "/api/v1/license", {})
    assert r.status_code == 200 and r.json()["status"] == "no_contract"

    today = date.today()
    r = client.post(
        "/contracts",
        data={"csrf": admin, "client_id": str(client_id), "device_id": "1", "plan_id": "1",
              "start_date": str(today - timedelta(days=1)), "end_date": str(today + timedelta(days=365)),
              "price_agreed": "15000000", "status": "active"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    # Heartbeat through the real agent code path.
    r = signed(client, agent, "/api/v1/heartbeat", {"app_version": "0.2.0", "cpu_temp": 51.5, "disk_pct": 20,
                                                      "room": "should-be-ignored"})
    assert r.status_code == 200 and r.json()["license_status"] == "active"

    # License is EdDSA-signed and verifiable with the public key from device.json.
    r = signed(client, agent, "/api/v1/license", {})
    claims = jwt.decode(r.json()["license_token"], LICENSE_PUBLIC_PEM, algorithms=["EdDSA"])
    assert claims["sub"] == device_id and claims["status"] == "active"
    assert claims["limits"]["max_participants"] == 10

    page = client.get(f"/devices/{device_id}")
    assert "0.2.0" in page.text and "°C" in page.text

    # Claim is one-time.
    r = client.post("/api/v1/claim/verify", json={"device_id": device_id, "claim_code": "wrong"})
    assert r.status_code == 401
    r = client.post("/api/v1/claim/verify", json={"device_id": device_id, "claim_code": claim_code})
    assert r.status_code == 200
    r = client.post("/api/v1/claim/verify", json={"device_id": device_id, "claim_code": claim_code})
    assert r.status_code == 401

    # Suspend -> license says suspended; reactivate -> active again.
    client.post(f"/devices/{device_id}/status", data={"csrf": admin, "action": "suspend"})
    assert signed(client, agent, "/api/v1/license", {}).json()["status"] == "suspended"
    client.post(f"/devices/{device_id}/status", data={"csrf": admin, "action": "reactivate"})
    assert signed(client, agent, "/api/v1/license", {}).json()["status"] == "active"

    # Regenerate QR invalidates nothing already used but issues a fresh one-time code.
    r = client.post(f"/devices/{device_id}/regenerate-claim", data={"csrf": admin})
    new_code = re.search(r"claim\?c=([A-Za-z0-9_-]+)", r.text).group(1)
    assert new_code != claim_code
    assert client.post("/api/v1/claim/verify", json={"device_id": device_id, "claim_code": new_code}).status_code == 200

    # Revoke needs the typed confirmation, then blocks the device entirely.
    client.post(f"/devices/{device_id}/status", data={"csrf": admin, "action": "revoke", "confirm_id": "nope"})
    assert signed(client, agent, "/api/v1/heartbeat", {}).status_code == 200
    client.post(f"/devices/{device_id}/status", data={"csrf": admin, "action": "revoke", "confirm_id": device_id})
    assert signed(client, agent, "/api/v1/heartbeat", {}).status_code == 403

    audit = client.get("/audit").text
    for action in ("device.created", "contract.created", "device.claimed", "device.suspend", "device.revoke"):
        assert action in audit


def test_bad_and_stale_signatures(client, admin):
    r = client.post("/devices", data={"csrf": admin})
    payload = json.loads(unescape(re.search(r"<pre>(.*?)</pre>", r.text, re.S).group(1)))
    agent = load_agent(payload["device_id"], "not-the-secret")
    assert signed(client, agent, "/api/v1/heartbeat", {}).status_code == 401

    agent = load_agent(payload["device_id"], payload["device_secret"])
    body = b"{}"
    headers = agent.sign("POST", "/api/v1/heartbeat", body)
    # Signature over a different body must fail (tampering).
    assert client.post("/api/v1/heartbeat", content=b'{"x":1}', headers=headers).status_code == 401
    # Replay of an old request must fail.
    real_time = time.time
    try:
        agent.time.time = lambda: real_time() - 3600
        old = agent.sign("POST", "/api/v1/heartbeat", body)
    finally:
        agent.time.time = real_time
    assert client.post("/api/v1/heartbeat", content=body, headers=old).status_code == 401


def test_every_form_carries_the_csrf_token(client, admin):
    pages = ["/", "/account", "/plans/new", "/clients/new", "/devices/new", "/contracts/new", "/devices/dev_missing",
             "/branding"]
    pages += [f"/clients/{c}" for c in set(re.findall(r'href="/clients/(\d+)"', client.get("/clients").text))]
    pages += [f"/devices/{d}" for d in set(re.findall(r'href="/devices/(dev_[0-9a-f]+)"', client.get("/devices").text))]
    for path in pages:
        html = client.get(path).text
        forms = html.count("<form")
        tokens = re.findall(r'name="csrf" value="([^"]*)"', html)
        assert len(tokens) == forms, path
        assert all(t == admin for t in tokens), path


def test_security_headers(client):
    r = client.get("/login")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"


def png_bytes(color, size=(800, 200)):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, format="PNG")
    return buf.getvalue()


def test_branding_logo_reaches_the_device(client, admin, tmp_path):
    import hashlib

    up = lambda files, **data: client.post(  # noqa: E731
        "/branding/logo", data={"csrf": admin, "action": "upload", **data}, files=files, follow_redirects=False
    )
    # Not an image -> rejected, nothing stored.
    assert up({"logo": ("x.png", b"<svg onload=alert(1)>", "image/png")}).status_code == 303
    assert "No logo yet" in client.get("/branding").text
    # CSRF still enforced on multipart.
    r = client.post("/branding/logo", data={"action": "upload"}, files={"logo": ("a.png", png_bytes("red"), "image/png")})
    assert r.status_code == 403

    assert up({"logo": ("global.png", png_bytes((255, 0, 0, 255)), "image/png")}).status_code == 303
    page = client.get("/branding").text
    src = re.search(r'src="(/branding/logo/\d+\.png)', page).group(1)
    img = client.get(src)
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    global_sha = hashlib.sha256(img.content).hexdigest()

    r = client.post("/clients", data={"csrf": admin, "name": "PT Logo"}, follow_redirects=False)
    client_id = int(r.headers["location"].rsplit("/", 1)[1])
    r = client.post("/devices", data={"csrf": admin, "client_id": str(client_id)})
    payload = json.loads(unescape(re.search(r"<pre>(.*?)</pre>", r.text, re.S).group(1)))
    agent = load_agent(payload["device_id"], payload["device_secret"])
    agent.KIOSK_DIR = str(tmp_path)
    agent.get = lambda path: client.get(path, headers=agent.sign("GET", path, b""))

    hb = signed(client, agent, "/api/v1/heartbeat", {}).json()
    assert hb["logo_sha256"] == global_sha and hb["client_name"] == "PT Logo" and hb["claimed"] is False
    assert hb["tagline"] is None

    # Tagline: global default, client override, empty override falls back to the default.
    tag = lambda text, **data: client.post(  # noqa: E731
        "/branding/tagline", data={"csrf": admin, "tagline": text, **data}, follow_redirects=False)
    assert tag("  Secure   Video Conferencing ").status_code == 303
    assert signed(client, agent, "/api/v1/heartbeat", {}).json()["tagline"] == "Secure Video Conferencing"
    tag("Private Meetings", client_id=str(client_id))
    assert signed(client, agent, "/api/v1/heartbeat", {}).json()["tagline"] == "Private Meetings"
    tag("", client_id=str(client_id))
    hb = signed(client, agent, "/api/v1/heartbeat", {}).json()
    assert hb["tagline"] == "Secure Video Conferencing"
    assert client.post("/branding/tagline", data={"tagline": "x"}).status_code == 403  # CSRF
    agent.update_kiosk(hb)
    assert hashlib.sha256((tmp_path / "logo.png").read_bytes()).hexdigest() == global_sha
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["client_name"] == "PT Logo" and state["claimed"] is False
    assert state["tagline"] == "Secure Video Conferencing"

    # A client logo overrides the default, and is resized for the 480 px panel.
    assert up({"logo": ("c.png", png_bytes((0, 0, 255, 255), (2000, 1000)), "image/png")},
              client_id=str(client_id)).status_code == 303
    hb = signed(client, agent, "/api/v1/heartbeat", {}).json()
    assert hb["logo_sha256"] != global_sha
    agent.update_kiosk(hb)
    from PIL import Image

    with Image.open(tmp_path / "logo.png") as im:
        assert max(im.size) <= 480 and im.getpixel((0, 0))[:3] == (0, 0, 255)

    # Logo endpoint is for signed devices only.
    assert client.get("/api/v1/branding/logo").status_code == 401

    # Removing both logos removes the file on the device.
    client.post("/branding/logo", data={"csrf": admin, "action": "delete", "client_id": str(client_id)})
    client.post("/branding/logo", data={"csrf": admin, "action": "delete"})
    hb = signed(client, agent, "/api/v1/heartbeat", {}).json()
    assert hb["logo_sha256"] is None
    agent.update_kiosk(hb)
    assert not (tmp_path / "logo.png").exists()
    audit = client.get("/audit").text
    assert "branding.logo_set" in audit and "branding.logo_removed" in audit and "branding.tagline_set" in audit


def self_signed(name):
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now_ = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(1).not_valid_before(now_).not_valid_after(now_ + timedelta(days=90)).sign(key, hashes.SHA256()))
    return (cert.public_bytes(serialization.Encoding.PEM).decode(),
            key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()).decode())


def test_site_id_dns_and_certificate(client, admin, monkeypatch):
    import cloudflare

    calls = []
    monkeypatch.setattr(cloudflare, "upsert_a", lambda name, ip: calls.append(("upsert", name, ip)) or True)
    monkeypatch.setattr(cloudflare, "delete_a", lambda name: calls.append(("delete", name)))

    r = client.post("/devices", data={"csrf": admin, "label": "Site test"})
    payload = json.loads(unescape(re.search(r"<pre>(.*?)</pre>", r.text, re.S).group(1)))
    dev = payload["device_id"]
    agent = load_agent(dev, payload["device_secret"])
    update = lambda **f: client.post(f"/devices/{dev}", data={"csrf": admin, "connectivity_mode": "standard", **f},  # noqa: E731
                                     follow_redirects=False)

    # Before any Site ID the device has its automatic name, and it's in the certificate list.
    auto = dev.replace("_", "-") + ".lan.example.test"
    hb = signed(client, agent, "/api/v1/heartbeat", {"lan_ip": "192.168.10.42"}).json()
    assert hb["site_fqdn"] == auto and hb["tls_sha256"] is None
    assert calls == [("upsert", auto, "192.168.10.42")]
    assert auto in open(os.environ["CERTS_WANTED"]).read()
    calls.clear()

    # Invalid and reserved names (including another device's automatic one) are refused.
    for bad in ("Ab", "-acme", "acme-", "a--b", "x" * 31, "www", "acme_1", "dev-0123abcd"):
        update(site_id=bad)
        assert signed(client, agent, "/api/v1/heartbeat", {}).json()["site_fqdn"] == auto, bad

    assert update(site_id="Acme-HQ").status_code == 303  # normalised to lowercase
    hb = signed(client, agent, "/api/v1/heartbeat", {"lan_ip": "192.168.10.42"}).json()
    assert hb["site_fqdn"] == "acme-hq.lan.example.test" and hb["tls_sha256"] is None
    # The automatic name's record goes, the Site ID's record takes over.
    assert calls == [("delete", auto), ("upsert", "acme-hq.lan.example.test", "192.168.10.42")]
    wanted = open(os.environ["CERTS_WANTED"]).read()
    assert "acme-hq.lan.example.test" in wanted and auto not in wanted
    calls.clear()

    # Same IP again: no DNS call. A public IP is never published.
    signed(client, agent, "/api/v1/heartbeat", {"lan_ip": "192.168.10.42"})
    signed(client, agent, "/api/v1/heartbeat", {"lan_ip": "8.8.8.8"})
    assert calls == []

    # Another device cannot take the same name.
    r2 = client.post("/devices", data={"csrf": admin})
    other = json.loads(unescape(re.search(r"<pre>(.*?)</pre>", r2.text, re.S).group(1)))
    client.post(f"/devices/{other['device_id']}", data={"csrf": admin, "connectivity_mode": "standard",
                                                        "site_id": "acme-hq"})
    other_agent = load_agent(other["device_id"], other["device_secret"])
    assert signed(client, other_agent, "/api/v1/heartbeat", {}).json()["site_fqdn"] == \
        other["device_id"].replace("_", "-") + ".lan.example.test"
    agent = load_agent(dev, payload["device_secret"])  # load_agent re-reads env; switch back

    # No certificate yet -> 404; once the worker publishes one, the device gets it.
    assert signed_get(client, agent, "/api/v1/tls").status_code == 404
    chain, key = self_signed("acme-hq.lan.example.test")
    d = os.path.join(os.environ["TLS_DIR"], "acme-hq.lan.example.test")
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "fullchain.pem"), "w").write(chain)
    open(os.path.join(d, "privkey.pem"), "w").write(key)
    hb = signed(client, agent, "/api/v1/heartbeat", {"lan_ip": "192.168.10.42"}).json()
    assert hb["tls_sha256"]
    tls = signed_get(client, agent, "/api/v1/tls").json()
    assert tls["fqdn"] == "acme-hq.lan.example.test" and tls["privkey"] == key and tls["sha256"] == hb["tls_sha256"]
    assert "valid until" in client.get(f"/devices/{dev}").text

    # The agent installs it and adds the Caddy site block, then reloads Caddy once.
    etc = os.path.join(TMP, "etc-appliance")
    os.makedirs(etc, exist_ok=True)
    agent.TLS_DIR, agent.SITE_FILE = os.path.join(etc, "tls"), os.path.join(etc, "site_fqdn")
    agent.CADDY_SITE = os.path.join(etc, "caddy-site.caddy")
    agent.get = lambda path: signed_get(client, agent, path)
    reloads = []
    agent.reload_caddy = lambda: reloads.append(1) or True
    agent.update_site(hb)
    assert open(agent.SITE_FILE).read() == "acme-hq.lan.example.test"
    assert open(os.path.join(agent.TLS_DIR, "privkey.pem")).read() == key
    assert oct(os.stat(os.path.join(agent.TLS_DIR, "privkey.pem")).st_mode & 0o777) == "0o600"
    assert "acme-hq.lan.example.test {" in open(agent.CADDY_SITE).read() and reloads == [1]
    agent.update_site(hb)  # nothing changed: no second reload
    assert reloads == [1]
    # The real reload forces Caddy to re-read certificate files even if its config is unchanged.
    sent = {}

    class Resp:
        status_code, text = 200, ""

    real_reload = load_agent(dev, payload["device_secret"]).reload_caddy
    real_reload.__globals__["CADDYFILE"] = os.path.join(etc, "Caddyfile")
    open(os.path.join(etc, "Caddyfile"), "w").write("example")
    real_reload.__globals__["requests"].post, saved = (lambda url, **kw: sent.update(url=url, **kw) or Resp()), \
        real_reload.__globals__["requests"].post
    try:
        assert real_reload() and sent["headers"]["Cache-Control"] == "must-revalidate"
        assert sent["url"].endswith("/load") and sent["data"] == b"example"
    finally:
        real_reload.__globals__["requests"].post = saved

    # Renaming removes the old DNS record and the old name from the certificate list.
    update(site_id="acme")
    assert ("delete", "acme-hq.lan.example.test") in calls
    wanted = open(os.environ["CERTS_WANTED"]).read()
    assert "acme.lan.example.test" in wanted and "acme-hq" not in wanted
    hb = signed(client, agent, "/api/v1/heartbeat", {"lan_ip": "192.168.10.42"}).json()
    assert hb["site_fqdn"] == "acme.lan.example.test" and hb["tls_sha256"] is None
    agent.update_site(hb)  # no certificate for the new name yet: stop serving the old block
    # ...and links fall back to the LAN IP until the new name has its certificate.
    assert not os.path.exists(agent.CADDY_SITE) and open(agent.SITE_FILE).read() == ""
    # Revoked devices drop off the list.
    client.post(f"/devices/{dev}/status", data={"csrf": admin, "action": "revoke", "confirm_id": dev})
    assert "acme.lan.example.test" not in open(os.environ["CERTS_WANTED"]).read()


def test_public_site_and_console_are_separate(client):
    site = {"host": "example.test"}
    r = client.get("/", headers=site)
    assert r.status_code == 200 and "never leave the room" in r.text and "Request a demo" in r.text
    assert r.headers["cache-control"].startswith("public") and "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert client.get("/", headers={"host": "www.example.test"}).status_code == 200
    # Nothing of the console is reachable on the public site...
    for path in ("/login", "/devices", "/branding", "/audit", "/account"):
        assert client.get(path, headers=site, follow_redirects=False).status_code == 404, path
    assert client.post("/login", headers=site, data={"email": EMAIL}).status_code == 404
    # ...but devices still talk to the API there, and assets load.
    assert client.get("/api/v1/license/pubkey", headers=site).text == LICENSE_PUBLIC_PEM
    assert client.get("/static/site/site.css", headers=site).status_code == 200
    assert "Allow: /" in client.get("/robots.txt", headers=site).text
    # The console host is never indexed and still shows the console.
    assert "Disallow: /" in client.get("/robots.txt").text
    console = client.get("/", follow_redirects=False)
    assert console.status_code in (200, 303) and "never leave the room" not in console.text
