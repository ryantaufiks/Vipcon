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

import jwt  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from models import User  # noqa: E402
from security import hash_password  # noqa: E402

LIVE = {}
LAGGING = {}
CLOSED = []


@pytest.fixture(scope="module")
def client():
    # camelCase + string int64, as protojson emits; plus one room whose summary says 0 but has people.
    main.list_rooms = lambda: [{"name": n, "numParticipants": c, "creationTime": "1800000000"} for n, c in LIVE.items()]
    main.list_participants = lambda room: {"participants": [{"identity": "x"}] * LAGGING.get(room, 0)}
    main.delete_room = lambda room: CLOSED.append(room)
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
    # The host sign-in link points at the office-network name, never the public one.
    site = tmp_path / "site_fqdn"
    site.write_text("client1.lan.example.com")
    monkeypatch.setattr(main, "SITE_FILE", str(site))
    r = client.get(f"/api/v1/meetings/now?k={today}", headers=public)
    assert r.json()["admin_url"] == "https://client1.lan.example.com/admin"
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


def test_meeting_info_for_the_guest_join_page(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_CONCURRENT_MEETINGS", 100)
    t = token(client)
    auth = {"Authorization": f"Bearer {t}"}
    m = client.post("/api/v1/meetings", json={"title": "Board review", "passcode": "135790", "lobby": True},
                    headers=auth).json()
    info = client.get(f"/api/v1/meeting-info?room={m['room_name']}").json()
    assert info["title"] == "Board review" and info["has_passcode"] is True and info["lobby"] is True
    assert "passcode" not in info and "room_name" not in info and "id" not in info
    assert client.get("/api/v1/meeting-info?room=nope").status_code == 404
    client.post(f"/api/v1/meetings/{m['id']}/end", headers=auth)
    assert client.get(f"/api/v1/meeting-info?room={m['room_name']}").status_code == 404
    assert CLOSED[-1] == m["room_name"]  # ending a meeting disconnects whoever is still in it


def test_new_users_are_validated(client):
    auth = {"Authorization": f"Bearer {token(client)}"}
    add = lambda **kw: client.post("/api/v1/users", json={"username": "newhost", "password": "long-enough-pw",  # noqa: E731
                                                           "role": "host", **kw}, headers=auth)
    assert add(username="X").status_code == 422
    assert add(password="short").status_code == 422
    assert add(password="newhost").status_code == 422
    assert add(role="owner").status_code == 422
    assert add(username=" NewHost ", display_name="  New Host ").status_code == 200
    users = {u["username"]: u for u in client.get("/api/v1/users", headers=auth).json()}
    assert users["newhost"]["display_name"] == "New Host" and users["newhost"]["role"] == "host"
    assert add().status_code == 409


def test_console_join_by_role(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_CONCURRENT_MEETINGS", 100)
    monkeypatch.setattr(main, "livekit_credentials", lambda: ("devkey", "devsecret"))
    admin = {"Authorization": f"Bearer {token(client)}"}
    for name, role in (("hosta", "host"), ("hostb", "host"), ("staff1", "member")):
        client.post("/api/v1/users", json={"username": name, "password": f"{name}-password", "role": role,
                                           "display_name": name.title()}, headers=admin)
    auth = {n: {"Authorization": "Bearer " + client.post("/api/v1/login", json={"username": n, "password": f"{n}-password"}).json()["token"]}
            for n in ("hosta", "hostb", "staff1")}

    m = client.post("/api/v1/meetings", json={"title": "Host A's review", "passcode": "112233", "lobby": True},
                    headers=auth["hosta"]).json()
    assert client.post("/api/v1/meetings", json={"title": "Not allowed"}, headers=auth["staff1"]).status_code == 403

    def join(who):
        return client.post("/api/v1/token", json={"room": m["room_name"], "identity": ""}, headers=who)

    def is_admin(r):
        claims = jwt.decode(r.json()["token"], options={"verify_signature": False})
        return claims["video"]["roomAdmin"]

    owner, other_host, staff, boss = join(auth["hosta"]), join(auth["hostb"]), join(auth["staff1"]), join(admin)
    assert owner.json()["host"] is True and is_admin(owner)
    assert boss.json()["host"] is True and is_admin(boss)  # the Client Admin moderates every meeting
    # Another host and staff join straight in (no passcode, no waiting room) but cannot moderate.
    for r in (other_host, staff):
        assert r.status_code == 200 and r.json()["host"] is False and not is_admin(r)
    assert jwt.decode(staff.json()["token"], options={"verify_signature": False})["sub"] == "Staff1"
    assert client.get(f"/api/v1/meetings/{m['id']}/lobby", headers=auth["hostb"]).status_code == 403
    assert client.post(f"/api/v1/meetings/{m['id']}/end", headers=auth["staff1"]).status_code == 403

    listed = {x["id"]: x for x in client.get("/api/v1/meetings", headers=auth["hostb"]).json()}
    assert listed[m["id"]]["can_host"] is False and listed[m["id"]]["host_name"] == "Hosta"
    listed = {x["id"]: x for x in client.get("/api/v1/meetings", headers=auth["hosta"]).json()}
    assert listed[m["id"]]["can_host"] is True

    client.post(f"/api/v1/meetings/{m['id']}/end", headers=auth["hosta"])
    assert join(auth["staff1"]).status_code == 410


# ---------------------------------------------------------------- passwords and recovery


def add_user(client, name, role, password=None):
    admin = {"Authorization": f"Bearer {token(client)}"}
    r = client.post("/api/v1/users", json={"username": name, "password": password or f"{name}-password-1", "role": role},
                    headers=admin)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def sign_in(client, name, password=None):
    r = client.post("/api/v1/login", json={"username": name, "password": password or f"{name}-password-1"})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def test_change_own_password_ends_other_sessions(client):
    add_user(client, "pwchange", "host")
    old, other = sign_in(client, "pwchange"), sign_in(client, "pwchange")
    r = client.post("/api/v1/me/password", json={"current_password": "wrong-password", "new_password": "a-new-password-1"},
                    headers=old)
    assert r.status_code == 401
    assert client.post("/api/v1/me/password", json={"current_password": "pwchange-password-1", "new_password": "short"},
                       headers=old).status_code == 422
    r = client.post("/api/v1/me/password", json={"current_password": "pwchange-password-1",
                                                  "new_password": "a-new-password-1"}, headers=old)
    assert r.status_code == 200
    assert client.get("/api/v1/me", headers=other).status_code == 401  # the other browser is signed out
    assert client.get("/api/v1/me", headers={"Authorization": "Bearer " + r.json()["token"]}).status_code == 200
    sign_in(client, "pwchange", "a-new-password-1")


def test_admin_resets_another_users_password(client):
    uid = add_user(client, "forgetful", "member")
    theirs = sign_in(client, "forgetful")
    admin = {"Authorization": f"Bearer {token(client)}"}
    me_id = client.get("/api/v1/me", headers=admin).json()["id"]
    assert client.post(f"/api/v1/users/{me_id}/password", json={"new_password": "x" * 12}, headers=admin).status_code == 400
    assert client.post(f"/api/v1/users/{uid}/password", json={"new_password": "temporary-pw-1"}, headers=theirs).status_code == 403
    assert client.post(f"/api/v1/users/{uid}/password", json={"new_password": "temporary-pw-1"}, headers=admin).status_code == 200
    assert client.get("/api/v1/me", headers=theirs).status_code == 401
    sign_in(client, "forgetful", "temporary-pw-1")
    events = client.get("/api/v1/security/events", headers=admin).json()
    assert any(e["action"] == "password.reset_by_admin" and e["detail"] == "forgetful" for e in events)


def test_a_second_admin_can_be_added(client):
    add_user(client, "admin2", "admin")
    assert client.get("/api/v1/users", headers=sign_in(client, "admin2")).status_code == 200


def test_recovery_kit_codes_reset_an_admin_password_once(client):
    main.RECOVERY_LIMIT.hits.clear()
    main.RECOVERY_LIMIT_ALL.hits.clear()
    add_user(client, "kitadmin", "admin")
    auth = sign_in(client, "kitadmin")
    assert client.get("/api/v1/me/recovery-codes", headers=auth).json()["remaining"] == 0
    assert client.post("/api/v1/me/recovery-codes", json={"password": "wrong"}, headers=auth).status_code == 401
    codes = client.post("/api/v1/me/recovery-codes", json={"password": "kitadmin-password-1"}, headers=auth).json()["codes"]
    assert len(codes) == 8 and all(len(c) == 11 and c[5] == "-" for c in codes) and len(set(codes)) == 8
    assert client.get("/api/v1/me/recovery-codes", headers=auth).json()["remaining"] == 8

    reset = lambda code, pw="recovered-password-1", **kw: client.post(  # noqa: E731
        "/api/v1/recovery/reset", json={"username": "kitadmin", "code": code, "new_password": pw}, **kw)
    assert reset(codes[0], headers={"x-vipcon-public": "1"}).status_code == 403  # office network only
    assert reset("AAAAA-AAAAA").status_code == 401
    assert reset(codes[0], pw="short").status_code == 422  # a valid code is not spent on a bad password
    r = reset(codes[0].lower().replace("-", " "))  # case and separators do not matter
    assert r.status_code == 200 and r.json()["remaining"] == 7
    assert client.get("/api/v1/me", headers=auth).status_code == 401  # old sessions end
    sign_in(client, "kitadmin", "recovered-password-1")
    assert reset(codes[0], pw="another-password-1").status_code == 401  # each code works once

    # Codes are for admins: a host's username gets nowhere.
    add_user(client, "kithost", "host")
    r = client.post("/api/v1/recovery/reset", json={"username": "kithost", "code": codes[1], "new_password": "x" * 12})
    assert r.status_code == 401

    # A new kit replaces the old one.
    auth = sign_in(client, "kitadmin", "recovered-password-1")
    fresh = client.post("/api/v1/me/recovery-codes", json={"password": "recovered-password-1"}, headers=auth).json()["codes"]
    assert reset(codes[1]).status_code == 401
    main.RECOVERY_LIMIT.hits.clear()
    assert reset(fresh[0], pw="third-password-1").status_code == 200


def test_recovery_codes_are_rate_limited(client):
    main.RECOVERY_LIMIT.hits.clear()
    main.RECOVERY_LIMIT_ALL.hits.clear()
    add_user(client, "slowadmin", "admin")
    codes = client.post("/api/v1/me/recovery-codes", json={"password": "slowadmin-password-1"},
                        headers=sign_in(client, "slowadmin")).json()["codes"]
    reset = lambda code: client.post("/api/v1/recovery/reset", json={  # noqa: E731
        "username": "slowadmin", "code": code, "new_password": "slow-password-1"})
    for _ in range(5):
        assert reset("BBBBB-BBBBB").status_code == 401
    assert reset(codes[0]).status_code == 429  # even the right code waits out the window
    main.RECOVERY_LIMIT.hits.clear()
    assert reset(codes[0]).status_code == 200


def test_device_screen_code_resets_an_admin_password(client, tmp_path, monkeypatch):
    import hashlib
    import json as _json
    import time as _time

    offer = tmp_path / "admin_reset.json"
    monkeypatch.setattr(main, "SCREEN_RESET_FILE", str(offer))
    add_user(client, "screenadmin", "admin")
    old = sign_in(client, "screenadmin")

    def show(code, nonce, expires_in=600):
        offer.write_text(_json.dumps({"nonce": nonce, "salt": "s4lt", "expires_at": _time.time() + expires_in,
                                      "hash": hashlib.sha256(("s4lt" + code).encode()).hexdigest()}))

    def use(code, user="screenadmin", **kw):
        return client.post("/api/v1/recovery/screen", json={"username": user, "code": code,
                                                            "new_password": "screen-password-1"}, **kw)

    assert use("12345678").status_code == 410  # nothing on screen
    show("12345678", "n1", expires_in=-1)
    assert use("12345678").status_code == 410  # expired
    show("12345678", "n1")
    assert use("12345678", headers={"x-vipcon-public": "1"}).status_code == 403
    assert use("12345678", user="pwchange").status_code == 401  # admins only
    assert use("1234 5678").status_code == 200
    assert client.get("/api/v1/me", headers=old).status_code == 401
    sign_in(client, "screenadmin", "screen-password-1")
    assert client.get("/healthz").json()["screen_reset_used"] == "n1"  # the kiosk clears its screen
    assert use("12345678").status_code == 410  # spent

    # Five wrong guesses burn the code.
    show("87654321", "n2")
    for _ in range(main.SCREEN_RESET_MAX_FAILURES):
        assert use("00000000").status_code == 401
    assert use("87654321").status_code == 410

    # The admin can switch the whole thing off.
    auth = sign_in(client, "screenadmin", "screen-password-1")
    assert client.post("/api/v1/settings", json={"screen_reset": False}, headers=auth).status_code == 200
    assert client.get("/healthz").json()["screen_reset"] is False
    show("11112222", "n3")
    assert use("11112222").status_code == 403
    client.post("/api/v1/settings", json={"screen_reset": True}, headers=auth)
    actions = [e["action"] for e in client.get("/api/v1/security/events", headers=auth).json()]
    assert "password.reset.device_screen" in actions and "screen_reset.disabled" in actions
