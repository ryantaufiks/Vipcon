"""Device app tests (no LiveKit needed: it is stubbed).

    pip install -r device/app/requirements.txt pytest httpx && pytest -q device/app
"""

import os
import sqlite3
import sys
import tempfile

TMP = tempfile.mkdtemp()
os.environ.update(DATABASE_URL=f"sqlite:///{TMP}/app.db", SESSION_SECRET="test-session-secret",
                  DEVICE_ID="dev_test", ROOT_DOMAIN="example.test", BACKUP_SCHEDULER="0",
                  BACKUP_STATE_FILE=f"{TMP}/backup.json")
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



# ---------------------------------------------------------------- two-factor sign-in


def totp_code(secret, offset=0):
    import time as _time
    from security import totp_at
    return totp_at(secret, int(_time.time() // 30) + offset)


def enroll_2fa(client, auth):
    r = client.post("/api/v1/me/2fa/setup", headers=auth)
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    assert r.json()["uri"].startswith("otpauth://totp/VIPCON:") and "<svg" in r.json()["qr_svg"]
    assert client.post("/api/v1/me/2fa/enable", json={"code": "000000"}, headers=auth).status_code == 401
    assert client.post("/api/v1/me/2fa/enable", json={"code": totp_code(secret)}, headers=auth).status_code == 200
    return secret


def test_two_factor_sign_in(client):
    add_user(client, "twofa", "host")
    auth = sign_in(client, "twofa")
    assert client.get("/api/v1/me", headers=auth).json()["totp_enabled"] is False
    secret = enroll_2fa(client, auth)
    assert client.get("/api/v1/me", headers=auth).json()["totp_enabled"] is True
    assert client.post("/api/v1/me/2fa/setup", headers=auth).status_code == 409

    login = lambda **kw: client.post("/api/v1/login", json={"username": "twofa", "password": "twofa-password-1", **kw})  # noqa: E731
    r = login()
    assert r.status_code == 401 and r.json()["detail"] == "totp required"
    assert login(totp="123456").json()["detail"] == "invalid totp"
    assert login(totp=totp_code(secret, 1)).status_code == 200
    assert login(totp=totp_code(secret, 1)).status_code == 401  # the same code never works twice
    assert login(totp=totp_code(secret, 0)).status_code == 401  # nor an older one
    assert login(password="wrong-password", totp=totp_code(secret, 1)).json()["detail"] == "invalid credentials"

    # Lost phone: a Client Admin switches it off, and their sessions end.
    admin = {"Authorization": f"Bearer {token(client)}"}
    uid = next(u["id"] for u in client.get("/api/v1/users", headers=admin).json() if u["username"] == "twofa")
    assert next(u for u in client.get("/api/v1/users", headers=admin).json() if u["id"] == uid)["totp_enabled"] is True
    assert client.post(f"/api/v1/users/{uid}/2fa/reset", headers=admin).status_code == 200
    assert login().status_code == 200


def test_policy_requires_two_factor_for_admins_and_hosts(client):
    add_user(client, "policyhost", "host")
    add_user(client, "policymember", "member")
    admin_user = "policyadmin"
    add_user(client, admin_user, "admin")
    admin = sign_in(client, admin_user)
    enroll_2fa(client, admin)
    assert client.post("/api/v1/settings", json={"require_2fa": True}, headers=admin).status_code == 200
    try:
        r = client.post("/api/v1/login", json={"username": "policyhost", "password": "policyhost-password-1"})
        assert r.status_code == 200 and r.json()["must_enroll_2fa"] is True
        host = {"Authorization": "Bearer " + r.json()["token"]}
        assert client.get("/api/v1/meetings", headers=host).json()["detail"] == "2fa enrollment required"
        assert client.get("/api/v1/me", headers=host).json()["must_enroll_2fa"] is True
        secret = enroll_2fa(client, host)
        assert client.get("/api/v1/meetings", headers=host).status_code == 200
        # Under the policy a host cannot switch it off again; members are not affected.
        r = client.post("/api/v1/me/2fa/disable", json={"password": "policyhost-password-1", "code": totp_code(secret, 1)},
                        headers=host)
        assert r.status_code == 403
        member = sign_in(client, "policymember")
        assert client.get("/api/v1/meetings", headers=member).status_code == 200
        msecret = enroll_2fa(client, member)
        r = client.post("/api/v1/me/2fa/disable", json={"password": "policymember-password-1", "code": totp_code(msecret, 1)},
                        headers=member)
        assert r.status_code == 200
    finally:
        client.post("/api/v1/settings", json={"require_2fa": False}, headers=admin)
    events = [e["action"] for e in client.get("/api/v1/security/events", headers=admin).json()]
    assert "2fa.required" in events and "2fa.enabled" in events and "2fa.disabled" in events


def test_recovery_code_reset_turns_two_factor_off(client):
    main.RECOVERY_LIMIT.hits.clear()
    main.RECOVERY_LIMIT_ALL.hits.clear()
    add_user(client, "lostphone", "admin")
    auth = sign_in(client, "lostphone")
    enroll_2fa(client, auth)
    codes = client.post("/api/v1/me/recovery-codes", json={"password": "lostphone-password-1"}, headers=auth).json()["codes"]
    r = client.post("/api/v1/recovery/reset", json={"username": "lostphone", "code": codes[0], "new_password": "found-it-again-1"})
    assert r.status_code == 200
    assert client.post("/api/v1/login", json={"username": "lostphone", "password": "found-it-again-1"}).status_code == 200


def test_optional_fields_accept_null_as_the_web_app_sends_them(client, monkeypatch):
    """The web app sends null for "no passcode" / "no time" / "no code"; that must not be a 422."""
    monkeypatch.setattr(main, "MAX_CONCURRENT_MEETINGS", 100)
    auth = {"Authorization": f"Bearer {token(client)}"}
    m = client.post("/api/v1/meetings", json={"title": "Open meeting", "passcode": None, "scheduled_at": None,
                                              "lobby": False}, headers=auth)
    assert m.status_code == 200, m.text
    assert m.json()["has_passcode"] is False and m.json()["scheduled_at"] is None
    monkeypatch.setattr(main, "livekit_credentials", lambda: ("devkey", "devsecret"))
    r = client.post("/api/v1/join", json={"room": m.json()["room_name"], "identity": "Guest", "passcode": None})
    assert r.status_code == 200, r.text
    r = client.post("/api/v1/login", json={"username": "host", "password": "host-password-1", "totp": None})
    assert r.status_code == 200, r.text
    assert client.post("/api/v1/settings", json={"org_name": None, "screen_reset": None}, headers=auth).status_code == 200


def test_encrypted_meetings_never_hold_the_key(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_CONCURRENT_MEETINGS", 100)
    auth = {"Authorization": f"Bearer {token(client)}"}
    m = client.post("/api/v1/meetings", json={"title": "Encrypted board", "e2ee": True, "lobby": False}, headers=auth).json()
    assert m["e2ee"] is True and "#" not in m["url"]  # the key is added by the host's browser, never by the device
    info = client.get(f"/api/v1/meeting-info?room={m['room_name']}").json()
    assert info["e2ee"] is True and not any("key" in k for k in info)
    plain = client.post("/api/v1/meetings", json={"title": "Plain", "lobby": False}, headers=auth).json()
    assert client.get(f"/api/v1/meeting-info?room={plain['room_name']}").json()["e2ee"] is False


def test_a_host_admits_only_into_their_own_meeting(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_CONCURRENT_MEETINGS", 100)
    monkeypatch.setattr(main, "livekit_credentials", lambda: ("devkey", "devsecret"))
    add_user(client, "lobbyhost1", "host")
    add_user(client, "lobbyhost2", "host")
    h1, h2 = sign_in(client, "lobbyhost1"), sign_in(client, "lobbyhost2")
    mine = client.post("/api/v1/meetings", json={"title": "Mine", "lobby": True}, headers=h1).json()
    theirs = client.post("/api/v1/meetings", json={"title": "Theirs", "lobby": True}, headers=h2).json()
    asked = client.post("/api/v1/join", json={"room": theirs["room_name"], "identity": "Visitor"}).json()
    assert asked["status"] == "waiting"
    req = client.get(f"/api/v1/meetings/{theirs['id']}/lobby", headers=h2).json()[0]
    q = f"room={theirs['room_name']}&identity=Visitor&ticket={asked['ticket']}"
    # Host 1 names their own meeting but host 2's waiting guest: refused, and the guest stays waiting.
    assert client.post(f"/api/v1/meetings/{mine['id']}/lobby/{req['id']}/admit", headers=h1).status_code == 404
    assert client.post(f"/api/v1/meetings/{mine['id']}/lobby/{req['id']}/reject", headers=h1).status_code == 200
    assert client.get(f"/api/v1/lobby/status?{q}").json() == {"status": "waiting"}
    assert client.post(f"/api/v1/meetings/{theirs['id']}/lobby/{req['id']}/admit", headers=h2).status_code == 200
    assert "token" in client.get(f"/api/v1/lobby/status?{q}").json()


def test_an_admission_survives_a_lost_poll(client, monkeypatch):
    """The guest's page may drop a poll's answer (a re-render, a flaky network): the admission must
    still be there on the next poll, or the guest waits forever."""
    monkeypatch.setattr(main, "MAX_CONCURRENT_MEETINGS", 100)
    monkeypatch.setattr(main, "livekit_credentials", lambda: ("devkey", "devsecret"))
    auth = {"Authorization": f"Bearer {token(client)}"}
    m = client.post("/api/v1/meetings", json={"title": "Lobby", "lobby": True}, headers=auth).json()
    tickets = {}
    status = lambda who: client.get(  # noqa: E731
        f"/api/v1/lobby/status?room={m['room_name']}&identity={who}&ticket={tickets[who]}").json()
    for who in ("Guest1", "Guest2"):
        asked = client.post("/api/v1/join", json={"room": m["room_name"], "identity": who}).json()
        assert asked["status"] == "waiting"
        tickets[who] = asked["ticket"]
    waiting = client.get(f"/api/v1/meetings/{m['id']}/lobby", headers=auth).json()
    assert [r["identity"] for r in waiting] == ["Guest1", "Guest2"]
    g1, g2 = (r["id"] for r in waiting)
    client.post(f"/api/v1/meetings/{m['id']}/lobby/{g1}/admit", headers=auth)
    client.post(f"/api/v1/meetings/{m['id']}/lobby/{g2}/reject", headers=auth)
    assert "token" in status("Guest1") and "token" in status("Guest1")  # asked twice: still admitted
    assert status("Guest2") == {"status": "rejected"} == status("Guest2")
    assert client.get(f"/api/v1/meetings/{m['id']}/lobby", headers=auth).json() == []  # host sees only waiting
    # Turned away, they may ask again: back in the queue.
    assert client.post("/api/v1/join", json={"room": m["room_name"], "identity": "Guest2"}).json()["status"] == "waiting"
    assert [r["identity"] for r in client.get(f"/api/v1/meetings/{m['id']}/lobby", headers=auth).json()] == ["Guest2"]


def test_only_the_guest_who_asked_collects_the_admission(client, monkeypatch):
    """Knowing a waiting guest's name is not enough to take their place."""
    monkeypatch.setattr(main, "MAX_CONCURRENT_MEETINGS", 100)
    monkeypatch.setattr(main, "livekit_credentials", lambda: ("devkey", "devsecret"))
    auth = {"Authorization": f"Bearer {token(client)}"}
    m = client.post("/api/v1/meetings", json={"title": "Tickets", "lobby": True}, headers=auth).json()
    asked = client.post("/api/v1/join", json={"room": m["room_name"], "identity": "Ann"}).json()
    req = client.get(f"/api/v1/meetings/{m['id']}/lobby", headers=auth).json()[0]
    client.post(f"/api/v1/meetings/{m['id']}/lobby/{req['id']}/admit", headers=auth)
    base = f"/api/v1/lobby/status?room={m['room_name']}&identity=Ann"
    assert client.get(base).status_code == 403
    assert client.get(base + "&ticket=guess").status_code == 403
    assert "token" in client.get(f"{base}&ticket={asked['ticket']}").json()


def test_a_removed_guest_cannot_walk_back_in(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_CONCURRENT_MEETINGS", 100)
    monkeypatch.setattr(main, "livekit_credentials", lambda: ("devkey", "devsecret"))
    removed = []
    monkeypatch.setattr(main, "remove_participant", lambda room, who: removed.append((room, who)))
    auth = {"Authorization": f"Bearer {token(client)}"}
    m = client.post("/api/v1/meetings", json={"title": "Kick", "lobby": True}, headers=auth).json()
    asked = client.post("/api/v1/join", json={"room": m["room_name"], "identity": "Troll"}).json()
    req = client.get(f"/api/v1/meetings/{m['id']}/lobby", headers=auth).json()[0]
    client.post(f"/api/v1/meetings/{m['id']}/lobby/{req['id']}/admit", headers=auth)
    status = f"/api/v1/lobby/status?room={m['room_name']}&identity=Troll&ticket={asked['ticket']}"
    assert "token" in client.get(status).json()
    assert client.post(f"/api/v1/meetings/{m['id']}/moderate/kick", json={"identity": "Troll"}, headers=auth).json() == {"ok": True}
    assert removed == [(m["room_name"], "Troll")]
    assert client.get(status).json() == {"status": "removed"}  # the old admission is gone
    # Asking again puts them back in the queue: the host decides again.
    again = client.post("/api/v1/join", json={"room": m["room_name"], "identity": "Troll"}).json()
    assert again["status"] == "waiting" and again["ticket"] != asked["ticket"]
    assert [r["identity"] for r in client.get(f"/api/v1/meetings/{m['id']}/lobby", headers=auth).json()] == ["Troll"]


def test_a_locked_meeting_takes_no_one_new(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_CONCURRENT_MEETINGS", 100)
    monkeypatch.setattr(main, "livekit_credentials", lambda: ("devkey", "devsecret"))
    host, other = sign_in(client, "lobbyhost1"), sign_in(client, "lobbyhost2")  # made by an earlier test (user limit)
    m = client.post("/api/v1/meetings", json={"title": "Lock", "lobby": False}, headers=host).json()
    assert client.post(f"/api/v1/meetings/{m['id']}/lock", json={"locked": True}, headers=other).status_code == 403
    assert client.post(f"/api/v1/meetings/{m['id']}/lock", json={"locked": True}, headers=host).json()["locked"] is True
    assert client.get(f"/api/v1/meeting-info?room={m['room_name']}").json()["locked"] is True
    assert client.post("/api/v1/join", json={"room": m["room_name"], "identity": "Late"}).status_code == 403
    assert client.post("/api/v1/token", json={"room": m["room_name"], "identity": "Host"}, headers=host).json()["host"] is True
    assert client.post("/api/v1/token", json={"room": m["room_name"], "identity": "Staff"}, headers=other).status_code == 403
    client.post(f"/api/v1/meetings/{m['id']}/lock", json={"locked": False}, headers=host)
    assert "token" in client.post("/api/v1/join", json={"room": m["room_name"], "identity": "Late"}).json()


def test_mute_all_spares_the_hosts_and_screen_audio(monkeypatch):
    import livekit_admin
    people = [
        {"identity": "Host", "metadata": '{"host": true}', "tracks": [{"sid": "TR_h", "type": "AUDIO", "source": "MICROPHONE"}]},
        {"identity": "Ann", "metadata": '{"host": false}', "tracks": [
            {"sid": "TR_screen", "type": "AUDIO", "source": "SCREEN_SHARE_AUDIO"},
            {"sid": "TR_ann", "type": "AUDIO", "source": "MICROPHONE"}]},
        {"identity": "Bob", "metadata": "", "tracks": [{"sid": "TR_bob", "type": "AUDIO", "source": "MICROPHONE", "muted": True}]},
        {"identity": "Cam", "metadata": "", "tracks": [{"sid": "TR_cam", "type": "VIDEO", "source": "CAMERA"}]},
    ]
    calls = []
    monkeypatch.setattr(livekit_admin, "list_participants", lambda room: {"participants": people})
    monkeypatch.setattr(livekit_admin, "_rpc", lambda method, body, room, grants=None: calls.append((method, body["identity"], body["track_sid"])) or {})
    assert livekit_admin.mute_all("r") == 1
    assert calls == [("MutePublishedTrack", "Ann", "TR_ann")]
    calls.clear()
    livekit_admin.mute_participant("r", "Ann", True)
    assert calls == [("MutePublishedTrack", "Ann", "TR_ann")]  # the microphone, not the shared screen's audio


def test_mute_all_needs_the_host(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_CONCURRENT_MEETINGS", 100)
    monkeypatch.setattr(main, "mute_all", lambda room: 3)
    owner, stranger = sign_in(client, "lobbyhost1"), sign_in(client, "lobbyhost2")
    m = client.post("/api/v1/meetings", json={"title": "Quiet"}, headers=owner).json()
    assert client.post(f"/api/v1/meetings/{m['id']}/moderate/mute-all", headers=stranger).status_code == 403
    assert client.post(f"/api/v1/meetings/{m['id']}/moderate/mute-all", headers=owner).json() == {"ok": True, "muted": 3}


# ---------------------------------------------------------------- encrypted backup (keep these last: restore swaps the DB)


class FakeRemote:
    """The control plane's backup store, in memory."""
    store = {}

    def upload(self, locator, blob):
        self.store.setdefault(locator, []).insert(0, blob)
        return {"ok": True}

    def versions(self, locator):
        return [{"name": str(i)} for i in range(len(self.store.get(locator, [])))]

    def fetch(self, locator, name):
        return self.store[locator][int(name)]

    def delete(self, locator):
        self.store.pop(locator, None)
        return {"ok": True}


def test_backup_key_round_trip():
    import backup
    locator, key = backup.new_key()
    text = backup.encode_key(locator, key)
    assert text.startswith("VBK1-")
    assert backup.decode_key(text.lower().replace("-", " ")) == (locator, key)
    sealed = backup.seal(locator, key, b"secret data")
    assert b"secret" not in sealed and backup.unseal(locator, key, sealed) == b"secret data"
    with pytest.raises(backup.BackupError):
        backup.unseal("0" * 32, key, sealed)  # the locator is bound in: no copying between kits
    with pytest.raises(backup.BackupError):
        backup.decode_key("VBK1-NOTAKEY")


def test_encrypted_backup_and_restore(client, monkeypatch):
    monkeypatch.setattr(main, "backup_remote", lambda: FakeRemote())
    monkeypatch.setattr(main, "CONTROL_PLANE_URL", "http://cp.test")
    FakeRemote.store.clear()
    admin = {"Authorization": f"Bearer {token(client)}"}
    assert client.get("/api/v1/backup", headers=admin).json()["enabled"] is False
    assert client.post("/api/v1/backup/enable", json={"password": "wrong"}, headers=admin).status_code == 401
    r = client.post("/api/v1/backup/enable", json={"password": "host-password-1"}, headers=admin)
    assert r.status_code == 200, r.text
    kit = r.json()["backup_key"]
    assert r.json()["status"]["last_ok_at"] and not r.json()["status"]["last_error"]
    assert client.post("/api/v1/backup/enable", json={"password": "host-password-1"}, headers=admin).status_code == 409
    (locator, blobs), = FakeRemote.store.items()
    assert b"host-password" not in blobs[0] and b"SQLite" not in blobs[0]  # sealed, not just compressed
    assert locator not in kit  # the kit is base32, not the raw locator
    import backup
    assert "key" not in client.get("/api/v1/backup", headers=admin).json()

    # Data written after the backup is gone after a restore; every session ends.
    add_user(client, "afterbackup", "member")
    assert client.post("/api/v1/backup/run", headers=admin).json()["last_ok_at"]
    FakeRemote.store[locator].insert(0, backup.seal(locator, b"\0" * 32, b"junk"))  # a newer copy with another key
    FakeRemote.store[locator].pop(1)  # drop the copy that has afterbackup; the original one remains
    bad = client.post("/api/v1/backup/restore", json={"backup_key": "VBK1-AAAA", "password": "host-password-1"}, headers=admin)
    assert bad.status_code == 422
    other = backup.encode_key(locator, b"\1" * 32)
    assert client.post("/api/v1/backup/restore", json={"backup_key": other, "password": "host-password-1"},
                       headers=admin).status_code == 409
    r = client.post("/api/v1/backup/restore", json={"backup_key": kit, "password": "host-password-1"}, headers=admin)
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/me", headers=admin).status_code == 401  # session epoch changed
    admin = {"Authorization": f"Bearer {token(client)}"}
    names = {u["username"] for u in client.get("/api/v1/users", headers=admin).json()}
    assert "afterbackup" not in names and "host" in names
    assert any(e["action"] == "backup.restored" for e in client.get("/api/v1/security/events", headers=admin).json())
    assert client.get("/api/v1/backup", headers=admin).json()["enabled"] is True  # carries on to the same place

    # Turning it off deletes the off-site copies and forgets the key.
    assert client.post("/api/v1/backup/disable", json={"password": "host-password-1"}, headers=admin).json()["remote_deleted"]
    assert FakeRemote.store == {} and client.get("/api/v1/backup", headers=admin).json()["enabled"] is False
