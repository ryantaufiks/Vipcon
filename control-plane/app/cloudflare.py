"""Minimal Cloudflare DNS client for the per-device LAN records (A, DNS only)."""

import json
import logging
import urllib.parse
import urllib.request

from config import CF_API_TOKEN, CF_ZONE_ID

API = "https://api.cloudflare.com/client/v4"
log = logging.getLogger("control-plane.cloudflare")


class CloudflareError(Exception):
    pass


def enabled():
    return bool(CF_API_TOKEN and CF_ZONE_ID)


def _call(method, path, body=None):
    req = urllib.request.Request(
        f"{API}/zones/{CF_ZONE_ID}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        raise CloudflareError(f"{method} {path}: HTTP {e.code} {e.read()[:200]!r}") from None
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise CloudflareError(f"{method} {path}: {e}") from None
    if not data.get("success"):
        raise CloudflareError(f"{method} {path}: {data.get('errors')}")
    return data.get("result")


def _find(name):
    q = urllib.parse.urlencode({"type": "A", "name": name})
    return _call("GET", f"/dns_records?{q}") or []


def upsert_a(name, ip):
    """Point name at ip (DNS only, short TTL). Returns True if something changed."""
    body = {"type": "A", "name": name, "content": ip, "ttl": 120, "proxied": False,
            "comment": "Vipcon device LAN address (managed by the control plane)"}
    records = _find(name)
    if records:
        if records[0].get("content") == ip and not records[0].get("proxied"):
            return False
        _call("PUT", f"/dns_records/{records[0]['id']}", body)
    else:
        _call("POST", "/dns_records", body)
    return True


def delete_a(name):
    for record in _find(name):
        _call("DELETE", f"/dns_records/{record['id']}")
