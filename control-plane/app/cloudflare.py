"""Minimal Cloudflare client: per-device LAN records (A, DNS only) and, for devices with internet
access, a named tunnel plus its public CNAME (proxied)."""

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
    return _api(method, f"/zones/{CF_ZONE_ID}{path}", body)


def _api(method, path, body=None):
    req = urllib.request.Request(
        f"{API}{path}",
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


PUBLIC_COMMENT = "Vipcon device public name (managed by the control plane)"


class NameTaken(CloudflareError):
    """The name already has DNS records the control plane didn't create; they are never touched."""


def _owned(record):
    return (record.get("comment") or "").startswith(PUBLIC_COMMENT)


def _records(name):
    q = urllib.parse.urlencode({"name": name})
    return _call("GET", f"/dns_records?{q}") or []


def foreign_records(name):
    """Records on name that someone else manages (a website, mail, a manual entry)."""
    return [r for r in _records(name) if not _owned(r)]


def _upsert(name, rtype, content, proxied):
    """Point name at content with a record the control plane owns (marked by its comment).
    Raises NameTaken, changing nothing, when the name already has records of anyone else's."""
    records = _records(name)
    if any(not _owned(r) for r in records):
        raise NameTaken(f"{name} already has DNS records the control plane does not manage")
    body = {"type": rtype, "name": name, "content": content, "ttl": 1 if proxied else 120,
            "proxied": proxied, "comment": PUBLIC_COMMENT}
    for extra in records[1:]:
        _call("DELETE", f"/dns_records/{extra['id']}")
    if records:
        r = records[0]
        if r.get("type") == rtype and r.get("content") == content and bool(r.get("proxied")) == proxied:
            return False
        _call("PUT", f"/dns_records/{r['id']}", body)
    else:
        _call("POST", "/dns_records", body)
    return True


def delete_name(name):
    """Remove the control plane's own records for name; anyone else's stay."""
    for record in _records(name):
        if _owned(record):
            _call("DELETE", f"/dns_records/{record['id']}")


# ---------------------------------------------------------------- tunnels (internet access)

_account_id = None


def account_id():
    global _account_id
    if not _account_id:
        from config import CF_ACCOUNT_ID

        _account_id = CF_ACCOUNT_ID or (_call("GET", "") or {}).get("account", {}).get("id")
        if not _account_id:
            raise CloudflareError("cannot determine the Cloudflare account id")
    return _account_id


def _tunnels(path="", method="GET", body=None):
    return _api(method, f"/accounts/{account_id()}/cfd_tunnel{path}", body)


def ensure_tunnel(name):
    """Return (tunnel_id, token) for a remotely managed tunnel called name, creating it if needed."""
    q = urllib.parse.urlencode({"name": name, "is_deleted": "false"})
    found = _tunnels(f"?{q}") or []
    if found:
        tunnel_id = found[0]["id"]
        return tunnel_id, _tunnels(f"/{tunnel_id}/token")
    created = _tunnels("", "POST", {"name": name, "config_src": "cloudflare"})
    token = created.get("token") or _tunnels(f"/{created['id']}/token")
    return created["id"], token


def route_tunnel(tunnel_id, hostname, service):
    """Send hostname through the tunnel to service on the device; everything else gets a 404."""
    if foreign_records(hostname):  # check first, so a taken name leaves the tunnel config alone too
        raise NameTaken(f"{hostname} already has DNS records the control plane does not manage")
    _tunnels(f"/{tunnel_id}/configurations", "PUT", {"config": {"ingress": [
        {"hostname": hostname, "service": service},
        {"service": "http_status:404"},
    ]}})
    return _upsert(hostname, "CNAME", f"{tunnel_id}.cfargotunnel.com", True)


def delete_tunnel(tunnel_id):
    try:
        _tunnels(f"/{tunnel_id}/connections", "DELETE")
    except CloudflareError:
        pass  # no active connections
    try:
        _tunnels(f"/{tunnel_id}", "DELETE")
    except CloudflareError as e:
        if "1003" not in str(e) and "not found" not in str(e).lower():
            raise
