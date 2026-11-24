# Vipcon Control Plane

Superadmin web + API that runs on the Tencent server. It manages plans, clients, devices,
contracts and licenses, and receives metadata-only heartbeats from appliances. It never sees
users, rooms, participants, chat or media.

## Run

```bash
cp .env.example .env          # fill in POSTGRES_PASSWORD and SECRET_KEY (openssl rand -hex 32)
docker compose up -d --build
docker compose exec app python cli.py create-superadmin you@example.com
```

The app listens on `127.0.0.1:${CP_PORT:-8100}` only. Publish it through Cloudflare Tunnel
(public hostname `control.<ROOT_DOMAIN>` → `http://localhost:8100`). Nothing else is exposed.

Back up the `appdata` volume: it holds `license_ed25519.pem`, the key that signs every license.
Losing it means re-provisioning every device with a new public key. Do not change `SECRET_KEY`
once devices exist: it encrypts the stored device secrets.

## Office-network names (Site ID)

Give a device a **Site ID** on its page (e.g. `acme`) and it becomes reachable on the client's
network at `https://acme.lan.<ROOT_DOMAIN>` with a trusted certificate, no CA to install:

- The app keeps a DNS-only `A` record `acme.lan.<ROOT_DOMAIN>` → the device's LAN IP (from its
  heartbeat; private IPv4 only) through the Cloudflare API.
- The `certs` service (certbot + Cloudflare DNS-01) issues and renews a Let's Encrypt
  certificate for it; the device fetches it from `GET /api/v1/tls` and reloads Caddy.

Needs `CF_API_TOKEN` (Zone → DNS → Edit on the zone only), `CF_ZONE_ID` and `LE_EMAIL` in `.env`.
Start with `LE_STAGING=1` (untrusted test certificates, no rate limits), then set it to `0`.
Routers with DNS-rebind protection may refuse names that resolve to private IPs; the device's
IP address (and the fallback `dev_….local` name) keep working.

## Internet access (one public link per device)

A plan with **Internet access** ticked, an active contract and the device's own switch (device
page → Internet access) give the device a public name `https://<site-id>.<ROOT_DOMAIN>` (or
`dev-<id>.<ROOT_DOMAIN>`). Meeting links and the room-screen QR then use it, so the same link
works in the room and from anywhere, and site routers with DNS-rebind protection don't matter.

- The app creates a Cloudflare Tunnel per device (`vipcon-<device id>`), routes the public name to
  it (proxied `CNAME`) and hands the device its token over the signed API (`GET /api/v1/tunnel`).
  The agent runs `cloudflared` and publishes the name only while the tunnel is connected.
- Through the tunnel the device serves only meeting pages and guest APIs; admin, claim and
  account APIs stay on the office network. The meetings-in-progress list needs the daily key
  from the room-screen QR.
- Switching it off, a plan without it, an expired contract, suspending or revoking the device
  removes the name and the tunnel.

Needs `Account → Cloudflare Tunnel → Edit` on the same `CF_API_TOKEN` (`CF_ACCOUNT_ID` is
optional). Media between remote guests and the device needs a TURN relay, which comes in phase 2
(see `docs/design-internet-access.md`).

## Superadmin

- Login is email + password + TOTP (mandatory). 5 failures lock the account for 15 minutes.
- `python cli.py reset-2fa <email>` / `reset-password <email>` from the server shell.
- Every superadmin action is written to the audit log.

## Device API

All device requests except claim are signed with the device secret:

```
X-Device-ID:  dev_xxxxxxxxxxxx
X-Timestamp:  unix seconds (±300 s accepted)
X-Signature:  hex(HMAC-SHA256(device_secret, timestamp \n METHOD \n path \n body))
```

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /api/v1/heartbeat` | signed | Metadata: version, temperature, disk, memory, uptime, LAN IP, cert expiry |
| `POST /api/v1/license` | signed | EdDSA JWT with `status`, `limits`, `exp` (≤ contract end) |
| `GET /api/v1/license/pubkey` | none | Public key; also written into every `device.json` |
| `POST /api/v1/claim/verify` | claim code | One-time; called by the device app during the claim flow |

License `status` is `active` only while the device is not suspended/revoked and has an active
contract covering today. Otherwise it is `suspended`, `revoked`, `expired` or `no_contract`; the
device should refuse new meetings but keep running meetings already in progress.

## Tests

```bash
pip install -r app/requirements.txt pytest httpx requests
pytest -q tests                                   # SQLite
TEST_DATABASE_URL=postgresql+psycopg2://... pytest -q tests   # empty throwaway Postgres DB
```
