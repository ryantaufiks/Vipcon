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
