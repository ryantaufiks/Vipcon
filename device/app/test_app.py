"""Device app tests (no LiveKit needed: it is stubbed).

    pip install -r device/app/requirements.txt pytest httpx && pytest -q device/app
"""

import os
import sqlite3
import sys
import tempfile

TMP = tempfile.mkdtemp()
os.environ.update(DATABASE_URL=f"sqlite:///{TMP}/app.db", SESSION_SECRET="test-session-secret",
                  DEVICE_ID="dev_test", ROOT_DOMAIN="example.test")
sys.path.insert(0, os.path.dirname(__file__))

# A database from before show_on_screen existed, to exercise the startup migration.
con = sqlite3.connect(f"{TMP}/app.db")
con.execute("CREATE TABLE meetings (id INTEGER PRIMARY KEY, title VARCHAR NOT NULL, room_name VARCHAR UNIQUE NOT NULL,"
            " passcode_hash VARCHAR, host_user_id INTEGER, scheduled_at DATETIME, duration_minutes INTEGER,"
            " lobby BOOLEAN, e2ee BOOLEAN, locked BOOLEAN, max_participants INTEGER, status VARCHAR,"
            " created_at DATETIME, ended_at DATETIME)")
con.execute("INSERT INTO meetings (title, room_name, lobby, status) VALUES ('Legacy', 'legacy-room', 1, 'scheduled')")
con.commit()
con.close()

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from models import User  # noqa: E402
from security import hash_password  # noqa: E402

LIVE = {}
LAGGING = {}


@pytest.fixture(scope="module")
def client():
    # camelCase + string int64, as protojson emits; plus one room whose summary says 0 but has people.
    main.list_rooms = lambda: [{"name": n, "numParticipants": c, "creationTime": "1800000000"} for n, c in LIVE.items()]
    main.list_participants = lambda room: {"participants": [{"identity": "x"}] * LAGGING.get(room, 0)}
    with TestClient(main.app) as c:
        db = main.SessionLocal()
        db.add(User(username="host", display_name="Host", password_hash=hash_password("host-password-1"), role="admin", active=True))
        db.commit()
        db.close()
        yield c


def token(client):
    return client.post("/api/v1/login", json={"username": "host", "password": "host-password-1"}).json()["token"]


def test_migration_added_show_on_screen(client):
    LIVE.clear()
    LIVE["legacy-room"] = 2
    titles = [m["title"] for m in client.get("/api/v1/meetings/now").json()["meetings"]]
    assert titles == ["Legacy"]  # old meetings default to visible


def test_meetings_now_lists_only_live_visible_meetings(client):
    auth = {"Authorization": "Bearer " + token(client)}
    make = lambda title, **kw: client.post("/api/v1/meetings", json={"title": title, **kw}, headers=auth).json()  # noqa: E731
    live = make("Board meeting", passcode="1234")
    hidden = make("Confidential", show_on_screen=False)
    idle = make("Later today")
    LIVE.clear()
    LIVE.update({live["room_name"]: 3, hidden["room_name"]: 2, idle["room_name"]: 0})

    data = client.get("/api/v1/meetings/now").json()["meetings"]
    assert [m["title"] for m in data] == ["Board meeting"]
    LAGGING[idle["room_name"]] = 1  # summary says 0, ListParticipants says 1
    assert {m["title"] for m in client.get("/api/v1/meetings/now").json()["meetings"]} == {"Board meeting", "Later today"}
    LAGGING.clear()
    assert data[0]["participants"] == 3 and data[0]["has_passcode"] is True and data[0]["started_at"]
    assert "passcode" not in data[0] and "passcode_hash" not in data[0]

    client.post(f"/api/v1/meetings/{live['id']}/end", headers=auth)
    assert client.get("/api/v1/meetings/now").json()["meetings"] == []


def test_meetings_now_when_livekit_is_down(client):
    def boom():
        raise Exception("down")
    saved, main.list_rooms = main.list_rooms, boom
    try:
        assert client.get("/api/v1/meetings/now").status_code == 503
    finally:
        main.list_rooms = saved


def test_internet_requests_need_the_room_key_for_meetings_now(client, tmp_path, monkeypatch):
    LIVE.clear()
    LIVE["legacy-room"] = 1
    seed = tmp_path / "room_seed"
    monkeypatch.setattr(main, "ROOM_SEED_FILE", str(seed))
    public = {"x-vipcon-public": "1"}
    # On the office network: no key needed.
    assert client.get("/api/v1/meetings/now").status_code == 200
    # Through the tunnel: refused without the key, and while there is no seed at all.
    assert client.get("/api/v1/meetings/now", headers=public).status_code == 403
    assert client.get("/api/v1/meetings/now?k=", headers=public).status_code == 403
    seed.write_text("s3cret-seed")
    today, yesterday = main.room_keys()
    assert client.get("/api/v1/meetings/now?k=wrong", headers=public).status_code == 403
    for key in (today, yesterday):
        r = client.get(f"/api/v1/meetings/now?k={key}", headers=public)
        assert r.status_code == 200 and [m["title"] for m in r.json()["meetings"]] == ["Legacy"]
    from datetime import datetime, timedelta, timezone
    stale = main.room_keys(datetime.now(timezone.utc) - timedelta(days=2))[0]
    assert client.get(f"/api/v1/meetings/now?k={stale}", headers=public).status_code == 403


def test_meeting_link_uses_the_public_name_while_the_tunnel_is_up(client, tmp_path, monkeypatch):
    public, site = tmp_path / "public_fqdn", tmp_path / "site_fqdn"
    site.write_text("client1.lan.example.test")
    monkeypatch.setattr(main, "PUBLIC_FILE", str(public))
    monkeypatch.setattr(main, "SITE_FILE", str(site))
    t = token(client)
    m = client.post("/api/v1/meetings", json={"title": "Remote review"}, headers={"Authorization": f"Bearer {t}"}).json()
    assert m["url"] == f"https://client1.lan.example.test/m/{m['room_name']}" == m["local_url"]
    public.write_text("client1.example.test\n")
    m = client.get("/api/v1/meetings", headers={"Authorization": f"Bearer {t}"}).json()
    m = next(x for x in m if x["title"] == "Remote review")
    assert m["url"] == f"https://client1.example.test/m/{m['room_name']}"
    assert m["local_url"] == f"https://client1.lan.example.test/m/{m['room_name']}"
    assert "internet_url" not in m


def test_wrong_passcodes_are_rate_limited_per_room(client):
    t = token(client)
    m = client.post("/api/v1/meetings", json={"title": "Locked down", "passcode": "246810"},
                    headers={"Authorization": f"Bearer {t}"}).json()
    join = lambda code: client.post("/api/v1/join", json={"room": m["room_name"], "identity": "guest",  # noqa: E731
                                                           "passcode": code})
    for _ in range(main.PASSCODE_MAX_FAILURES):
        assert join("000000").status_code == 401
    assert join("246810").status_code == 429  # even the right code waits out the window
    main.PASSCODE_FAILURES.clear()
    assert join("246810").status_code == 200
