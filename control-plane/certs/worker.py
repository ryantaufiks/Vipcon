"""Certificate worker (runs in the certbot/dns-cloudflare image next to the control plane).

Every few minutes: for each name the app lists in CERTS_WANTED, make sure a Let's Encrypt
certificate exists and is not close to expiry (DNS-01 through Cloudflare, so the device never
needs to be reachable from the internet), then publish fullchain.pem/privkey.pem to TLS_DIR
where the app serves them to the device over the signed device API.

LE_STAGING=1 (default) uses Let's Encrypt's staging CA: untrusted certificates but no rate
limits. Switch to 0 once a staging certificate has been issued successfully.
"""

import json
import os
import shutil
import subprocess
import time

WANTED = os.environ.get("CERTS_WANTED", "/data/certs/wanted.txt")
TLS_DIR = os.environ.get("TLS_DIR", "/tls")
LIVE = "/etc/letsencrypt/live"
FAILURES = "/etc/letsencrypt/vipcon-failures.json"
CREDENTIALS = "/tmp/cloudflare.ini"
TOKEN = os.environ.get("CF_API_TOKEN", "")
EMAIL = os.environ.get("LE_EMAIL", "")
STAGING = os.environ.get("LE_STAGING", "1") == "1"
INTERVAL = int(os.environ.get("CERTS_INTERVAL", "300"))
BACKOFF = int(os.environ.get("CERTS_BACKOFF", "3600"))  # stay well inside LE's failed-validation limit
APP_UID = int(os.environ.get("APP_UID", "10001"))       # the control-plane app user


def log(*args):
    print("certs:", *args, flush=True)


def wanted():
    try:
        with open(WANTED) as f:
            return sorted({line.strip() for line in f if line.strip()})
    except OSError:
        return []


def load_failures():
    try:
        with open(FAILURES) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def issued_by_staging(name):
    try:
        with open(os.path.join(LIVE, name, "cert.pem"), "rb") as f:
            pem = f.read()
    except OSError:
        return False
    from cryptography import x509

    issuer = x509.load_pem_x509_certificate(pem).issuer.rfc4514_string()
    return "STAGING" in issuer.upper() or "(STAGING)" in issuer


def ensure(name):
    cmd = ["certbot", "certonly", "--non-interactive", "--agree-tos", "--email", EMAIL,
           "--dns-cloudflare", "--dns-cloudflare-credentials", CREDENTIALS,
           "--dns-cloudflare-propagation-seconds", "30",
           "--cert-name", name, "-d", name, "--key-type", "ecdsa", "--keep-until-expiring"]
    if STAGING:
        cmd.append("--test-cert")
    elif issued_by_staging(name):
        cmd.append("--force-renewal")  # replace a staging certificate with a trusted one
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        log(f"{name}: certbot failed: {(p.stderr or p.stdout).strip()[-400:]}")
    return p.returncode == 0


def publish(name):
    """Copy the live certificate to TLS_DIR/<name>/ if it changed, readable by the app only."""
    src = os.path.join(LIVE, name)
    dst = os.path.join(TLS_DIR, name)
    os.makedirs(dst, exist_ok=True)
    changed = False
    for fname, mode in (("fullchain.pem", 0o640), ("privkey.pem", 0o600)):
        s, d = os.path.join(src, fname), os.path.join(dst, fname)
        with open(s, "rb") as f:
            data = f.read()
        try:
            with open(d, "rb") as f:
                if f.read() == data:
                    continue
        except OSError:
            pass
        tmp = d + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.chown(tmp, APP_UID, APP_UID)
        os.chmod(tmp, mode)
        os.replace(tmp, d)
        changed = True
    os.chown(dst, APP_UID, APP_UID)
    os.chmod(dst, 0o750)
    if changed:
        log(f"{name}: published")


def prune(names):
    for entry in os.listdir(TLS_DIR) if os.path.isdir(TLS_DIR) else []:
        if entry not in names:
            shutil.rmtree(os.path.join(TLS_DIR, entry), ignore_errors=True)
            log(f"{entry}: removed (no longer wanted)")


def main():
    if not TOKEN or not EMAIL:
        log("CF_API_TOKEN and LE_EMAIL are not set; idle")
        while True:
            time.sleep(3600)
    with open(CREDENTIALS, "w") as f:
        f.write(f"dns_cloudflare_api_token = {TOKEN}\n")
    os.chmod(CREDENTIALS, 0o600)
    log("staging CA" if STAGING else "production CA")
    while True:
        names = wanted()
        failures = load_failures()
        for name in names:
            if time.time() - failures.get(name, 0) < BACKOFF:
                continue
            if ensure(name):
                failures.pop(name, None)
                publish(name)
            else:
                failures[name] = time.time()
        with open(FAILURES, "w") as f:
            json.dump(failures, f)
        prune(names)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
