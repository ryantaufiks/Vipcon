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
# Shared with the certs worker: it reads the wanted list and writes fullchain/privkey here.
CERTS_WANTED = env("CERTS_WANTED", "/data/certs/wanted.txt")
TLS_DIR = env("TLS_DIR", "/tls")

if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY is required (generate one with: openssl rand -hex 32)")
