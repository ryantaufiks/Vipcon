import os
import time

import jwt
import requests

LIVEKIT_HTTP = os.environ.get("LIVEKIT_HTTP", "http://127.0.0.1:7880")


def _read(path):
    with open(path) as f:
        return f.read().strip()


def _credentials():
    key = os.environ.get("LIVEKIT_API_KEY") or _read("/etc/appliance/livekit_key")
    secret = os.environ.get("LIVEKIT_API_SECRET") or _read("/etc/appliance/livekit_secret")
    return key, secret


def _admin_token(room, grants=None):
    key, secret = _credentials()
    now = int(time.time())
    payload = {
        "iss": key,
        "sub": "admin",
        "nbf": now,
        "exp": now + 300,
        "video": grants or {"roomJoin": True, "room": room, "roomAdmin": True},
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def _rpc(method, body, room, grants=None):
    token = _admin_token(room, grants)
    url = f"{LIVEKIT_HTTP}/twirp/livekit.RoomService/{method}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    r = requests.post(url, json=body, headers=headers, timeout=10)
    if r.status_code != 200:
        raise Exception(f"{method} failed: {r.status_code} {r.text[:200]}")
    return r.json()


def list_rooms():
    """Rooms LiveKit currently has open, with their participant counts."""
    return _rpc("ListRooms", {}, None, grants={"roomList": True}).get("rooms", [])


def list_participants(room):
    return _rpc("ListParticipants", {"room": room}, room)


def remove_participant(room, identity):
    return _rpc("RemoveParticipant", {"room": room, "identity": identity}, room)


def mute_participant(room, identity, muted):
    parts = list_participants(room).get("participants", [])
    track_sid = None
    for p in parts:
        if p.get("identity") == identity:
            for t in p.get("tracks", []):
                if t.get("type") == "AUDIO":
                    track_sid = t.get("sid")
                    break
    if not track_sid:
        raise Exception("microphone track not found")
    return _rpc(
        "MutePublishedTrack",
        {"room": room, "identity": identity, "track_sid": track_sid, "muted": muted},
        room,
    )
