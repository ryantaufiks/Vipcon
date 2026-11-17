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
