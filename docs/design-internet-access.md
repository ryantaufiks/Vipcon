# Design: one meeting link that works everywhere

Status: phase 1 (tunnel + one link) implemented; phases 2 and 3 not started.

## Goal

A meeting has **one link**. The same link works for:

- people in the room, on the office Wi-Fi (media stays on the LAN);
- people on a guest Wi-Fi or mobile data in the same building;
- people in another city or country.

Nobody has to choose between a "local" and an "internet" link, configure a router, or open a port.
The link also has to work on networks whose router filters private-IP DNS answers (the problem at
the Singapore demo site, where `client1.lan.xrostr.com` resolved through Cloudflare but the
site router returned nothing).

## Today

| Piece | Today | Problem |
|---|---|---|
| Web app + API | Caddy on the Pi, `https://<name>.lan.<root>` → LAN IP | Reachable only on the same LAN; breaks behind DNS rebind protection |
| Signalling (`/rtc`) | Same origin | Same |
| Media (WebRTC) | LiveKit on the Pi, `node_ip` = LAN IP, `use_external_ip: false` | Only LAN candidates, so remote clients can't connect |
| `internet_url` | `https://{DEVICE_ID}.{ROOT_DOMAIN}/m/…` | Placeholder; points nowhere |
| `tunnel_ok` in heartbeat | Always `null` | Placeholder |

## Design

```
                      Cloudflare edge                       our server
 remote guest ──HTTPS/WSS──▶ client1.xrostr.com ──tunnel──┐     TURN relay
      │                                                    │   (UDP 3478, TLS 443)
      └────────── media (DTLS-SRTP, via TURN if needed) ───┼────────▲
                                                           ▼        │
 guest in room ─HTTPS/WSS─▶ (same link) ──────────────▶  Pi: Caddy → app / LiveKit
      └────────── media direct over the LAN ──────────────▶ Pi (host candidate)
```

### 1. Public name per device, through a Cloudflare Tunnel

- Each device gets a **public name** `https://<site>.<root>` (for example `client1.xrostr.com`),
  falling back to `dev-<id>.<root>`, the same label rule as the `.lan` names.
  One level under the root, so Cloudflare's free Universal SSL covers it.
  Labels used by the site (`www`, `console`, `api`, …) are already reserved.
- The control plane creates a **named tunnel per device** through the Cloudflare API, a proxied
  CNAME `<name>.<root>` → `<tunnel-id>.cfargotunnel.com`, and an ingress rule pointing to the
  Pi's Caddy (`http://127.0.0.1:8081`, a localhost-only plain-HTTP listener used only by the tunnel).
  Renaming the Site ID moves the CNAME, like the `.lan` A record today.
- The device fetches its tunnel token over the signed device API (never in `device.json`) and the
  agent runs `cloudflared` as a new compose service. Heartbeat reports a real `tunnel_ok`.
- Nothing listens on the Pi's WAN side and no port forwarding is needed: the tunnel is outbound.

Signalling (`/rtc` WebSocket) and the web app go through the tunnel. Media does not: Cloudflare
Tunnel carries HTTP and WebSockets, not WebRTC UDP.

### 2. Media: direct when possible, relayed when not

LiveKit on the Pi offers three kinds of ICE candidate, and the browser picks the best that works:

1. **Host (LAN)**: a phone on the same network connects directly. Media never leaves the room,
   even though the page and signalling came through the public name.
2. **Server-reflexive (STUN)**: set `use_external_ip: true` so the Pi also advertises its public
   address. Many remote clients can then reach it directly through the two NATs.
3. **Relay (TURN)**: for strict NATs, guest Wi-Fi with client isolation, and corporate firewalls.
   A TURN relay on **our server** listens on UDP 3478 and TURN-over-TLS on 443, so it works
   wherever HTTPS works. LiveKit hands the TURN servers to clients
   (`rtc.turn_servers`). Credentials are short-lived (TURN REST shared secret), never static.
   To verify: LiveKit's support for a shared `secret` in `turn_servers` in v1.13.

The relay only sees DTLS-SRTP ciphertext; it can't decode audio or video.

### 3. Links and the room screen

- The meeting page, the admin "share link", and the room QR all use the **public link** when the
  tunnel is up. `local_url` / `internet_url` collapse into one `url`.
- When the tunnel is down (site has a LAN but no internet), links fall back to today's
  `.lan` name, then the IP, so in-room meetings keep working offline.
- This also removes the DNS rebind problem: the public name resolves to Cloudflare, not to a
  private IP, so site routers don't filter it.

### 4. Controls

- **Plan feature** `internet_access` (on/off) and a per-device switch in the console. With it off,
  no tunnel is created and behaviour is today's.
- **Exposure**: through the tunnel, by default only the meeting paths (`/m/*`, join API, `/rtc`,
  static assets). `/admin` and the claim page stay LAN-only unless enabled (open decision below).
- **Abuse limits**: rate limits on join/passcode attempts at the app; Cloudflare WAF rules on the
  public names.
- **Metering**: TURN relay bytes per device, reported in the console, so relay cost is visible
  per client and can be capped per plan.

## Cost and capacity

Relayed media costs bandwidth on our server, in both directions. Rough figure: a 720p camera is
about 1.5 Mbit/s up and the SFU sends each viewer one layer per other camera. A meeting with
4 remote guests relayed for an hour is around **10 GB** of relay traffic. Only relayed guests count;
direct (LAN or STUN) guests cost nothing. The Pi's uplink also carries every remote guest's
downstream, so the site's upload bandwidth limits how many remote guests a room can have.

## Compliance copy

The landing page says "No third-party processors". With internet access on, Cloudflare terminates
TLS for the page and signalling (it already serves DNS). Media stays end-to-end encrypted between
browser and Pi. The copy needs a qualifier such as "media never leaves your device unencrypted;
internet access is optional and uses Cloudflare for connectivity".

## Phase 1 as built

- Control plane: plan feature `internet_access`, device switch (`internet_enabled`), tunnel id,
  encrypted token and routed name on the device row; `reconcile_tunnel` runs on every heartbeat
  and on admin changes and makes no Cloudflare call when nothing changed.
- Device: the agent image carries `cloudflared` (pinned), runs it with the token in its
  environment (not on the command line) and writes `/etc/appliance/public_fqdn` only while
  `cloudflared`'s `/ready` answers. Caddy's tunnel listener is `127.0.0.1:8081`; it marks
  requests with `X-Vipcon-Public: 1`, which every other listener strips.
- Through the tunnel: meeting pages, `/rtc`, `/api/v1/join`, `/api/v1/lobby/status` and
  `/api/v1/meetings/now`. Everything else under `/api`, `/admin`, `/claim`, `/` and `/healthz`
  answers 404. Hosts sign in on the office network; from outside they join as guests.
- The meetings-in-progress list over the internet needs the key in the room-screen QR: an HMAC of
  the UTC date with a seed in `/etc/appliance/room_seed`; today's and yesterday's keys work.
- Wrong passcodes are limited per meeting (10 per 10 minutes), since links can now be opened
  from anywhere.
- LiveKit is unchanged in phase 1 (LAN candidates only). In-room guests on the same network get
  media directly. Remote guests and isolated guest Wi-Fi need the phase 2 relay; advertising a
  STUN address (`use_external_ip`) was left out until it's tested on the Pi, so it can't replace
  the LAN candidates.

## Phases

1. **Tunnel + one link.** Per-device tunnel, public name, `cloudflared` on the Pi, unified links,
   QR fallback. Fixes in-room access on any router; remote guests can open the page and join
   signalling, and get media once phase 2 is in.
2. **TURN relay.** Relay on our server with short-lived credentials and firewall rules; LiveKit
   config; metering. Makes remote access reliable on strict networks.
3. **Controls.** Plan feature, per-device switch, exposure settings, rate limits, console
   metering, compliance copy.

## Prerequisites from the owner

- A Cloudflare API token with **Account → Cloudflare Tunnel: Edit** (plus the existing
  **Zone → DNS: Edit**) for the control plane. Created by the owner and put in the server's `.env`.
- Firewall rules on the server for UDP 3478 and TCP 443 (and the TURN relay port range).
- The server's monthly traffic allowance, to size relay limits.

## Decisions

- `/admin` and the claim page: **LAN only**. Through the tunnel, only the meeting paths are exposed.
- Internet access is a **plan feature** (`internet_access`) with a per-device switch.
- TURN relay: **self-hosted on our server** (Tencent Lighthouse, ap-singapore, 2 vCPU / 4 GB) to
  start. Relay traffic counts against the server's monthly allowance (overage USD 0.081/GB in
  Singapore), and the plan's peak bandwidth caps how many guests can be relayed at the same time
  (about 4–5 Mbit/s each). Moving to a managed TURN later is a configuration change.
