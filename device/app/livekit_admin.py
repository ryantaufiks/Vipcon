import json
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


def delete_room(room):
    """Close the room: everyone still in it is disconnected."""
    return _rpc("DeleteRoom", {"room": room}, room, grants={"roomCreate": True})


def remove_participant(room, identity):
    return _rpc("RemoveParticipant", {"room": room, "identity": identity}, room)


def _mic(participant):
    """The participant's microphone track (not a screen share's audio), if they publish one."""
    tracks = participant.get("tracks", [])
    for t in tracks:
        if t.get("source") in ("MICROPHONE", 2):
            return t
    for t in tracks:  # older servers may leave the source out
        if t.get("type") in ("AUDIO", 1) and t.get("source") in (None, "UNKNOWN", 0):
            return t
    return None


def _is_host(participant):
    try:
        return bool(json.loads(participant.get("metadata") or "{}").get("host"))
    except ValueError:
        return False


def _mute(room, identity, track, muted=True):
    return _rpc("MutePublishedTrack", {"room": room, "identity": identity, "track_sid": track.get("sid"), "muted": muted}, room)


def mute_participant(room, identity, muted):
    for p in list_participants(room).get("participants", []):
        if p.get("identity") == identity:
            mic = _mic(p)
            if mic:
                return _mute(room, identity, mic, muted)
    raise Exception("microphone track not found")


def mute_all(room):
    """Mute every unmuted microphone except the hosts'. Returns how many were muted."""
    count = 0
    for p in list_participants(room).get("participants", []):
        mic = _mic(p)
        if mic and not mic.get("muted") and not _is_host(p):
            _mute(room, p.get("identity"), mic)
            count += 1
    return count
