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
        data={"csrf": admin, "name": "Standard", "connectivity_mode": "standard", "price_monthly": "1.500.000",
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
    assert f"https://{device_id}.example.test/claim?c={claim_code}" in unescape(r.text)

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
    pages = ["/", "/account", "/plans/new", "/clients/new", "/devices/new", "/contracts/new", "/devices/dev_missing"]
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
