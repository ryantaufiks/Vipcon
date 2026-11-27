import os


def env(name, default=None):
    value = os.environ.get(name)
    return value if value not in (None, "") else default


DATABASE_URL = env("DATABASE_URL", "sqlite:///./control.db")
ROOT_DOMAIN = env("ROOT_DOMAIN", "domainutama.com")
CONTROL_PLANE_URL = env("CONTROL_PLANE_URL", f"https://control.{ROOT_DOMAIN}")

# Signs superadmin sessions and derives the key that encrypts device secrets at rest.
SECRET_KEY = env("SECRET_KEY")
# Ed25519 private key (PEM) that signs license tokens; generated on first start if missing.
LICENSE_KEY_PATH = env("LICENSE_KEY_PATH", "./license_ed25519.pem")
LICENSE_TTL_DAYS = int(env("LICENSE_TTL_DAYS", "14"))

# Set to 0 only for local http development; production sits behind HTTPS.
COOKIE_SECURE = env("COOKIE_SECURE", "1") == "1"
SESSION_HOURS = int(env("SESSION_HOURS", "8"))

# A device is shown as online when its last heartbeat is newer than this.
ONLINE_WINDOW_SECONDS = int(env("ONLINE_WINDOW_SECONDS", "120"))
# Signed device requests older or newer than this are rejected (replay window).
SIGNATURE_MAX_SKEW = int(env("SIGNATURE_MAX_SKEW", "300"))
HEARTBEAT_RETENTION_DAYS = int(env("HEARTBEAT_RETENTION_DAYS", "30"))

# Short per-device name on the office network: https://<site_id>.<LAN_LABEL>.<ROOT_DOMAIN>
# resolves (public DNS, DNS-only record) to the device's LAN IP and has a Let's Encrypt cert.
LAN_LABEL = env("LAN_LABEL", "lan")
# Cloudflare token with Zone:DNS:Edit on ROOT_DOMAIN's zone; empty disables DNS + certificates.
CF_API_TOKEN = env("CF_API_TOKEN")
CF_ZONE_ID = env("CF_ZONE_ID")
# Internet access (public name per device through a Cloudflare Tunnel) also needs
# Account:Cloudflare Tunnel:Edit on the same token. The account id is looked up from the zone
# when not set.
CF_ACCOUNT_ID = env("CF_ACCOUNT_ID")
# The tunnel delivers requests to this listener on the device (Caddy, localhost only).
TUNNEL_SERVICE = env("TUNNEL_SERVICE", "http://127.0.0.1:8081")
# Media for guests outside the office: browsers relay audio and video through our TURN server
# (coturn, see docker-compose.yml). Devices with internet access get the shared secret and hand
# browsers short-lived credentials derived from it (TURN REST). Empty disables the relay.
TURN_SECRET = env("TURN_SECRET")
TURN_HOST = env("TURN_HOST", f"turn.{ROOT_DOMAIN}")
TURN_PORT = int(env("TURN_PORT", "3478"))
# Lifetime of the credentials a browser gets (seconds); LiveKit caps it at 24 h.
TURN_TTL = int(env("TURN_TTL", "14400"))
# TURN over TLS for networks that only allow HTTPS: a DNS name with a certificate (issued by the
# certs worker, like the devices' names) and port 443. Offered to devices once the certificate
# exists. Empty: UDP/TCP only.
TURN_TLS_HOST = env("TURN_TLS_HOST")
TURN_TLS_PORT = int(env("TURN_TLS_PORT", "443"))
# Shared with the certs worker: it reads the wanted list and writes fullchain/privkey here.
CERTS_WANTED = env("CERTS_WANTED", "/data/certs/wanted.txt")
TLS_DIR = env("TLS_DIR", "/tls")

# Public product site. On these hosts only the landing page, /api/* (devices) and /static are
# served; the superadmin console answers on any other host (e.g. console.<ROOT_DOMAIN>).
SITE_HOSTS = {h.strip().lower() for h in env("SITE_HOSTS", f"{ROOT_DOMAIN},www.{ROOT_DOMAIN}").split(",") if h.strip()}
SITE_CONTACT_EMAIL = env("SITE_CONTACT_EMAIL", "")
# Device data backups: sealed (AES-256-GCM) on the device with a key only the client holds; stored
# here as opaque files, one folder per backup locator (a random id that is part of the client's key).
DEVICE_BACKUP_DIR = env("DEVICE_BACKUP_DIR", "/data/device-backups")
DEVICE_BACKUP_MAX_BYTES = int(env("DEVICE_BACKUP_MAX_BYTES", str(25 * 1024 * 1024)))
DEVICE_BACKUP_KEEP = int(env("DEVICE_BACKUP_KEEP", "7"))

if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY is required (generate one with: openssl rand -hex 32)")
