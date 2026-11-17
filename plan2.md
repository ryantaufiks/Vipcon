# PLAN v2: Secure VIP Video Conference Appliance (Raspberry Pi 4B + LiveKit)

Revisi dari plan.md. Jitsi diganti **LiveKit** agar stack lebih ringan, UI lebih bebas dikustom, dan konfigurasi jaringan lebih sederhana.

---

## 0. Ringkasan Perubahan dari plan.md

| Area | plan.md (Jitsi) | plan2.md (LiveKit) |
|---|---|---|
| Komponen meeting di Pi | Prosody + Jicofo + JVB + Jitsi Web | **1 service: livekit-server** |
| UI meeting | Jitsi IFrame API | **Custom penuh** (LiveKit JS SDK / React components) |
| Auth peserta | JWT Prosody | **Access token LiveKit** (JWT, dibuat app lokal) |
| Lobby / raise hand / moderasi | Bawaan Jitsi | **Dibangun sendiri** di app (lihat bagian 6) |
| Port media | UDP 10000 + TCP 4443 | **UDP 7882 + TCP 7881** |
| TURN | coturn di Tencent | **TURN bawaan LiveKit** atau coturn |
| Redis | tidak ada | Tidak diperlukan (single node) |
| Pekerjaan UI | Lebih sedikit | **Lebih banyak** (+3–5 hari) |
| Beban RAM Pi | Lebih besar | Lebih kecil |

Bagian yang **tidak berubah**: control plane Tencent, QR/claim flow, lisensi, pemisahan data superadmin vs Admin Client. (Tunnel ditetapkan: **Cloudflare Tunnel** untuk dashboard + signaling saja; media via TURN.)

---

## 1. Tujuan & Prinsip

**Tujuan:** Perangkat plug-and-play untuk meeting VIP/eksklusif. Admin Client colok daya + LAN, scan QR, login, lalu mengelola user dan meeting sendiri.

**Prinsip:**
1. **Zero-touch deployment**: Pi nyala → service jalan otomatis → tunnel tersambung.
2. **Pemisahan data**: Superadmin hanya melihat metadata, tidak pernah isi/akses meeting.
3. **Identitas hardware tetap**: Device ID + QR hanya diubah/dihapus superadmin.
4. **Local-first**: Meeting LAN tetap jalan tanpa internet.
5. **Secret tidak ada di QR**: QR hanya berisi pointer + claim code sekali pakai.

**Di luar scope MVP:** recording/egress (terlalu berat untuk Pi), transcoding, lebih dari ~10 peserta, secure element/TPM.

---

## 2. Arsitektur

```
                ┌──────────────────────────────────┐
                │  TENCENT CLOUD (2c / 2GB)        │
                │  Control Plane                   │
                │  - Superadmin Web + API          │
                │  - Postgres                      │
                │  - Cloudflare Tunnel (dash+WS)   │
                │  - TURN (livekit-turn / coturn)  │
                │  - Caddy (control plane + TURN)  │
                └───────────────┬──────────────────┘
                                │ Cloudflare Tunnel (dashboard + WSS signaling)
       ┌────────────────────────┼────────────────────────┐
┌──────▼───────┐         ┌──────▼───────┐         ┌──────▼───────┐
│ Pi #1        │         │ Pi #2        │         │ Pi #N        │
│ livekit      │         │ livekit      │         │ livekit      │
│ app + caddy  │         │ app + caddy  │         │ app + caddy  │
│ agent        │         │ agent        │         │ agent        │
└──────────────┘         └──────────────┘         └──────────────┘
```

**Batas visibilitas Superadmin**

| Data | Superadmin lihat? |
|---|---|
| Plan, harga, kontrak, masa aktif | Ya |
| Online/offline, versi, suhu CPU, disk | Ya |
| Jumlah meeting / durasi agregat (opsional) | Ya (metadata saja) |
| Nama user, chat, daftar peserta, nama room | **Tidak** |
| Audio/video | **Tidak** |
| Akses dashboard Admin Client | **Tidak** (tidak ada akun superadmin di Pi) |

---

## 3. Stack Teknologi

| Layer | Pilihan | Catatan |
|---|---|---|
| Control plane backend | FastAPI atau Django | Pilih yang paling dikuasai |
| Control plane DB | PostgreSQL 16 | Cukup di 2 GB RAM |
| Control plane UI | HTMX/Alpine atau React ringan | Panel superadmin |
| Reverse proxy | Caddy | TLS wildcard via DNS-01 |
| Tunnel | **Cloudflare Tunnel** | Dashboard + WSS signaling saja; media via TURN (lihat bagian 10) |
| TURN | TURN bawaan LiveKit (di Pi/Tencent) atau coturn | Lihat bagian 9 |
| OS Pi | Raspberry Pi OS Lite 64-bit (Bookworm) | Headless |
| Container | Docker + Compose | |
| Media server | **livekit-server** (ARM64) | Satu container |
| Pi app backend | FastAPI/Django + SQLite | Dashboard + token issuer + moderasi |
| Pi UI | **React + @livekit/components-react** atau JS SDK murni | UI modern custom |
| Update | OTA bertanda tangan (cosign/minisign) | Wajib persetujuan Admin Client |

---

## 4. Phase Rencana Kerja

### Phase 0: Persiapan (1–2 hari)
- [ ] Domain utama + DNS provider ber-API (Cloudflare/DNSPod) untuk DNS-01.
- [ ] Tencent Cloud 2c/2GB: buka port 80, 443, 3478 & 5349 (TURN) + range relay; SSH dibatasi. (Tanpa frp/WG — tunnel via Cloudflare.)
- [ ] 1 Raspberry Pi 4B (4/8 GB), SSD USB atau SD A2, heatsink + fan, PSU 5V/3A.
- [ ] Repo Git: `control-plane/`, `device/`, `deploy/`, `docs/`.

### Phase 1: Validasi LiveKit di Pi (1–2 hari) ← MULAI DI SINI
- [ ] Jalankan livekit-server di Pi (contoh compose di bagian 5).
- [ ] Buat token uji (CLI `lk` atau skrip), join dari 2 perangkat via LAN.
- [ ] Ukur CPU/suhu pada 2, 4, 6, 8 peserta (kamera 720p).
- [ ] Putuskan batas peserta realistis per plan.

### Phase 2: Control Plane MVP (3–5 hari)
- [ ] Model: `Plan`, `Client`, `Device`, `Contract`, `Heartbeat`, `AuditLog`, `SuperAdmin`.
- [ ] Auth superadmin (password + TOTP 2FA wajib).
- [ ] CRUD Plan (nama, harga, max peserta, max user, durasi).
- [ ] CRUD Client + Contract (mulai, akhir, status).
- [ ] Generate Device: `device_id`, `claim_code` sekali pakai, `device_secret` (dikirim sekali saat provisioning, di server hanya hash).
- [ ] Generate QR (PNG/SVG + PDF label cetak): `https://<device_id>.domainutama.com/claim?c=<claim_code>`
- [ ] Endpoint heartbeat (`POST /api/v1/heartbeat`) dengan HMAC/mTLS.
- [ ] Endpoint license: mengembalikan JWT (EdDSA) dengan `exp` pendek (mis. 7 hari).
- [ ] Dashboard monitoring: online/offline, last seen, versi, suhu, disk, sisa kontrak.
- [ ] Audit log semua aksi superadmin.

### Phase 3: Device Base Image (3–4 hari)
- [ ] Raspberry Pi OS Lite 64-bit, hardening dasar (SSH key only, ufw, unattended-upgrades terbatas).
- [ ] Docker + Compose.
- [ ] Compose stack: `livekit`, `app` (dashboard + token issuer), `caddy` (TLS), `agent` (tunnel + heartbeat + license).
- [ ] systemd `appliance.service` → `docker compose up -d` saat boot; restart `unless-stopped`.
- [ ] Hardware watchdog (`/dev/watchdog`) + health check.
- [ ] First-boot provisioning: baca `/boot/firmware/device.json`, simpan ke `/etc/appliance/`, hapus dari boot partition.
- [ ] Log rotation + tmpfs untuk log berat (kurangi wear SD).

### Phase 4: Tunnel Cloudflare & Domain (2–3 hari)
- [ ] Wildcard DNS `*.domainutama.com` di Cloudflare (TLS otomatis di edge Cloudflare).
- [ ] Buat tunnel per device: token cloudflared dibuat saat provisioning, ditulis ke `device.json`; cloudflared outbound dari Pi.
- [ ] Routing `https://<device_id>.domainutama.com` → Pi (dashboard + WSS signaling LiveKit 7880). **Tidak** expose SSH/admin.
- [ ] Reconnect otomatis + backoff (cloudflared via systemd, restart `unless-stopped`).
- [ ] Catatan: Cloudflare hanya HTTP/WS (dashboard + signaling). Media meeting internet via TURN (Phase 10).
- [ ] Uji dari internet: dashboard terbuka, signaling tersambung.

### Phase 5: Akses Lokal HTTPS (2 hari)
Kamera/mic browser wajib HTTPS.
- [ ] `<device_id>.local.domainutama.com` → IP LAN Pi (agent update record DNS via API saat IP berubah).
- [ ] Sertifikat Let's Encrypt DNS-01 di Caddy Pi, auto-renew.
- [ ] Pi menampilkan IP/link LAN di dashboard.
- [ ] Uji meeting lokal dengan WAN dicabut.
- [ ] Catatan: perpanjangan sertifikat (~60 hari) butuh internet sesekali. Untuk lingkungan air-gapped penuh, gunakan CA privat (CA dipasang di tiap perangkat peserta).

### Phase 6: Dashboard Admin Client di Pi (5–7 hari)
- [ ] **Claim flow**: scan QR → verifikasi `claim_code` ke control plane → buat akun Admin pertama → claim_code hangus.
- [ ] Login (password + opsional TOTP), rate limit, lockout.
- [ ] Ganti password sendiri.
- [ ] CRUD user (role: admin, host, member, guest).
- [ ] Create meeting: judul, jadwal, room acak, passcode, lobby, max peserta (sesuai plan), E2EE on/off.
- [ ] Link join: lokal vs internet.
- [ ] Riwayat meeting + **hapus history** (hard delete + vacuum).
- [ ] Pengaturan organisasi: nama, logo, tema.
- [ ] Halaman status: tunnel, lisensi, suhu, versi, internet.
- [ ] **Token issuer**: endpoint yang menerbitkan access token LiveKit per peserta dengan grants sesuai role (lihat bagian 6).

### Phase 7: UI Meeting Custom + Fitur Moderasi (6–9 hari)
Bagian terbesar yang berbeda dari Jitsi. Lihat daftar fitur di bagian 6.
- [ ] Pre-join screen (cek mic/kamera, pilih perangkat, nama tampil).
- [ ] Room UI: grid/speaker view, kontrol mic/kamera/share screen, chat, daftar peserta.
- [ ] Fitur moderasi dan lobby (bagian 6).
- [ ] Responsif (desktop, tablet, mobile browser).
- [ ] Branding per client.
- [ ] Penanganan jaringan buruk: indikator koneksi, reconnect, adaptive stream/dynacast.

### Phase 8: Lisensi, Kontrak, Kill-Switch (2–3 hari)
- [ ] Agent ambil license token berkala; simpan terenkripsi.
- [ ] Mode grace: offline dari control plane tetap jalan sampai `exp` token (7–14 hari) agar meeting lokal tidak mati karena internet putus.
- [ ] Kontrak habis/suspend → tolak meeting baru, lalu nonaktif setelah grace. Pesan jelas ke Admin Client.
- [ ] Batas plan dipaksa di app (max user, max peserta).
- [ ] Ini **pencegah**, bukan DRM mutlak.

### Phase 9: Keamanan & Privasi (3–4 hari, paralel)
- [ ] Tidak ada akun/SSH superadmin di Pi. Akses support hanya via **support mode** yang diaktifkan Admin Client (token sementara, auto-expire, tercatat).
- [ ] Heartbeat hanya metadata (tanpa nama user/room/peserta).
- [ ] E2EE LiveKit (shared key) sebagai opsi; kunci dibagikan lewat link/passcode, bukan lewat server pusat.
- [ ] OTA update bertanda tangan + persetujuan Admin Client + changelog.
- [ ] Enkripsi data at-rest (LUKS partisi data, atau field-level untuk data sensitif).
- [ ] Hardening web: CSP, CSRF, cookie `HttpOnly/Secure/SameSite`, fail2ban.
- [ ] Verifikasi tunnel tidak membuka jalur di luar rencana.
- [ ] API key/secret LiveKit hanya di Pi, di-generate saat first boot, tidak pernah dikirim ke control plane.

### Phase 10: Media untuk Meeting Internet (2–3 hari)
Tunnel HTTP **tidak** membawa media UDP.
- [ ] Opsi 1: port-forward UDP 7882 + TCP 7881 di router klien.
- [ ] Opsi 2: **TURN** (TLS 443/5349) di Tencent agar peserta di balik NAT/firewall ketat tetap bisa masuk. Media tetap terenkripsi DTLS-SRTP.
- [ ] Opsi 3: fallback TCP 7881 lewat tunnel TCP (frp); kualitas lebih rendah.
- [ ] Atur `rtc.node_ip` / external IP sesuai skenario (bagian 5).
- [ ] Uji dari 4G dan jaringan kantor berfirewall.
- [ ] Pantau bandwidth Tencent bila TURN dipakai.

### Phase 11: Testing, Packaging, Serah Terima (3–5 hari)
- [ ] Skenario uji end-to-end (bagian 8).
- [ ] Load test 4/6/8/10 peserta.
- [ ] **Golden image** (`.img`) + skrip provisioning massal.
- [ ] Cetak label QR + stiker Device ID.
- [ ] Panduan Admin Client (PDF 1 halaman, 6 langkah).
- [ ] Runbook superadmin: provisioning, suspend, ganti QR, RMA.

---

## 5. Konfigurasi LiveKit di Pi

### 5.1 `livekit.yaml` (mode LAN + internet via port-forward/TURN eksternal)

```yaml
port: 7880
bind_addresses:
  - ""
rtc:
  tcp_port: 7881
  udp_port: 7882
  use_external_ip: false      # false = tanpa STUN (cocok LAN/offline)
  node_ip: 192.168.1.50       # diisi otomatis oleh agent dari IP LAN Pi
  # Untuk akses internet via port-forward, isi IP publik klien di sini
  # atau gunakan TURN eksternal (lihat turn_servers di bawah)
keys:
  APIKEY_DIISI_SAAT_FIRST_BOOT: SECRET_DIISI_SAAT_FIRST_BOOT
logging:
  level: info
room:
  empty_timeout: 300
  max_participants: 10        # ikut batas plan, diset oleh app
# Jika memakai TURN eksternal (Tencent), tambahkan:
# rtc:
#   turn_servers:
#     - host: turn.domainutama.com
#       port: 5349
#       protocol: tls
#       username: <dinamis>
#       credential: <dinamis>
```

> Catatan: nama field dan perilaku bisa berubah antar versi. Cek dokumentasi LiveKit untuk versi yang dipakai sebelum finalisasi, terutama bagian `rtc` dan TURN.

### 5.2 `docker-compose.yml` (device)

```yaml
services:
  livekit:
    image: livekit/livekit-server:latest   # kunci ke tag versi tertentu saat produksi
    command: --config /etc/livekit.yaml
    network_mode: host                     # disarankan untuk WebRTC/UDP
    restart: unless-stopped
    volumes:
      - ./livekit.yaml:/etc/livekit.yaml:ro

  app:
    build: ./app
    restart: unless-stopped
    environment:
      LIVEKIT_URL: ws://127.0.0.1:7880
      LIVEKIT_API_KEY_FILE: /etc/appliance/livekit_key
      LIVEKIT_API_SECRET_FILE: /etc/appliance/livekit_secret
    volumes:
      - appdata:/data
      - /etc/appliance:/etc/appliance:ro
    network_mode: host

  caddy:
    image: caddy:2
    restart: unless-stopped
    network_mode: host
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddydata:/data

  agent:
    build: ./agent                         # tunnel + heartbeat + license + DNS updater
    restart: unless-stopped
    network_mode: host
    volumes:
      - /etc/appliance:/etc/appliance

volumes:
  appdata:
  caddydata:
```

### 5.3 Caddyfile (sketsa)

> Dengan Cloudflare Tunnel, TLS domain publik dihentikan di edge Cloudflare. Caddy di Pi utamanya melayani LAN (`<device_id>.local.domainutama.com`) + origin lokal untuk tunnel.

```
<device_id>.domainutama.com, <device_id>.local.domainutama.com {
    tls {
        dns <provider> {env.DNS_API_TOKEN}
    }
    handle /rtc* {
        reverse_proxy 127.0.0.1:7880    # WSS signaling LiveKit
    }
    handle /api/* {
        reverse_proxy 127.0.0.1:8000    # app dashboard/API
    }
    handle {
        reverse_proxy 127.0.0.1:8000
    }
}
```

> Path signaling LiveKit bisa berbeda per versi/SDK. Uji dan sesuaikan.

---

## 6. Fitur Meeting: Bawaan vs Dibangun Sendiri

| Fitur | Status di LiveKit | Cara implementasi |
|---|---|---|
| Audio/video, mute, kamera | Bawaan | SDK |
| Share screen | Bawaan | SDK |
| Chat | Bawaan (komponen) / data channel | SDK / `publishData` |
| Daftar peserta, speaker aktif | Bawaan | SDK |
| Adaptive stream, simulcast, dynacast | Bawaan | Aktifkan di room options |
| E2EE | Didukung | Shared key (opsional) |
| **Lobby / waiting room** | Tidak ada | App menahan token; peserta masuk "antrean" di app, host approve → baru token diterbitkan |
| **Raise hand** | Tidak ada | Data channel / participant metadata |
| **Reaction** | Tidak ada | Data channel |
| **Kick peserta** | Via server API | `RoomService.RemoveParticipant` dari app |
| **Mute peserta / mute all** | Via server API | `MutePublishedTrack` |
| **Promote/demote host** | Via server API | `UpdateParticipant` (permission) |
| **Kunci room** | Dibangun | Tolak penerbitan token baru |
| **Passcode** | Dibangun | Verifikasi di app sebelum token |
| **Recording** | Butuh Egress (berat) | Di luar scope Pi 4B |

**Pola auth:** peserta tidak pernah memegang API secret. App Pi menerbitkan token berumur pendek dengan grants minimal (`roomJoin`, `room`, `canPublish`, `canSubscribe`, `canPublishData`) sesuai role.

---

## 7. Skema Database

### Control Plane (Postgres)

```sql
plans(id, name, price_monthly, price_yearly, max_participants, max_users,
      features jsonb, active)

clients(id, name, contact_email, contact_phone, notes, created_at)

devices(id, device_id UNIQUE, client_id NULL, plan_id NULL,
        secret_hash, claim_code_hash, claimed_at NULL,
        status ENUM('inventory','provisioned','claimed','suspended','revoked'),
        qr_version INT, created_at)

contracts(id, client_id, device_id, plan_id, start_date, end_date,
          status ENUM('active','suspended','expired'), price_agreed)

heartbeats(id, device_id, ts, app_version, cpu_temp, disk_pct, mem_pct,
           uptime, lan_ip, tunnel_ok bool, meetings_count_24h INT)

license_tokens(id, device_id, issued_at, expires_at, jti)

audit_logs(id, actor, action, target, ts, ip, detail jsonb)

superadmins(id, email, password_hash, totp_secret, created_at)
```

### Device (SQLite)

```sql
org_settings(id, org_name, logo_path, theme jsonb)
users(id, username UNIQUE, display_name, password_hash, role, totp_secret NULL,
      active, created_at, last_login)
meetings(id, title, room_name UNIQUE, passcode_hash NULL, host_user_id,
         scheduled_at, lobby bool, e2ee bool, locked bool, max_participants,
         created_at, ended_at)
lobby_requests(id, meeting_id, display_name, requested_at, status)  -- sementara, dihapus saat meeting selesai
meeting_events(id, meeting_id, ts, type, detail)                    -- minimal; bisa dimatikan
sessions(id, user_id, token_hash, expires_at, ip)
license_state(id, token_encrypted, expires_at, last_checked)
local_audit(id, user_id, action, ts)
```

---

## 8. Alur Penting

### 8.1 Provisioning (Superadmin)
1. Buat Device → generate `device_id`, `device_secret`, `claim_code`.
2. Keluarkan `device.json` + QR PNG/PDF.
3. Tulis `device.json` ke boot partition SD, flash image.
4. Tempel QR di casing. Status: `provisioned`.
5. First boot di Pi: generate API key/secret LiveKit lokal.

### 8.2 Serah Terima (Admin Client)
1. Nyalakan Pi → 2. colok LAN → 3. service naik otomatis, tunnel tersambung → 4. scan QR → 5. buat akun Admin (claim_code hangus) → 6. login, ganti password, buat user/meeting, kelola history.

### 8.3 Join Meeting
- **Lokal:** `https://<device_id>.local.domainutama.com/m/<room>` → app verifikasi (passcode/lobby) → token → LiveKit via LAN.
- **Internet:** `https://<device_id>.domainutama.com/m/<room>` → signaling via tunnel → media via port-forward/TURN.

---

## 9. Struktur Repo

```
vip-vc/
├── plan2.md
├── control-plane/
│   ├── app/
│   ├── migrations/
│   ├── docker-compose.yml
│   └── caddy/Caddyfile
├── device/
│   ├── app/              # dashboard + token issuer + moderasi API
│   ├── web/              # UI meeting (React + LiveKit components)
│   ├── agent/            # tunnel + heartbeat + license + DNS updater
│   ├── livekit/livekit.yaml.tpl
│   ├── Caddyfile.tpl
│   ├── docker-compose.yml
│   ├── systemd/appliance.service
│   └── provisioning/
├── image-builder/
├── docs/
│   ├── handover-guide.md
│   └── runbook.md
└── scripts/
```

---

## 10. Checklist Pengujian

**Fungsional**
- [ ] Boot dingin → semua service naik < 2 menit.
- [ ] Cabut daya mendadak → pulih tanpa korupsi data.
- [ ] Claim code tidak bisa dipakai dua kali.
- [ ] QR lama tidak berfungsi setelah regenerate.
- [ ] CRUD user/meeting; hapus history benar-benar menghapus data.
- [ ] Meeting lokal dengan WAN dicabut.
- [ ] Meeting dari 4G / jaringan lain.
- [ ] Campuran peserta lokal + internet.
- [ ] Lobby: peserta tidak bisa masuk sebelum di-approve.
- [ ] Kick/mute/mute all/lock room bekerja.

**Keamanan & privasi**
- [ ] Superadmin tidak punya jalur login ke dashboard/Pi.
- [ ] Heartbeat tidak memuat data user/room.
- [ ] Port scan eksternal: hanya yang direncanakan terbuka.
- [ ] Token LiveKit kedaluwarsa, tidak bisa dipakai ulang di room lain.
- [ ] Lisensi expired → perilaku sesuai rencana.
- [ ] E2EE on/off berfungsi.

**Performa**
- [ ] 4/6/8/10 peserta: CPU, suhu, packet loss, kualitas.
- [ ] Suhu stabil < 75°C dengan casing + fan.
- [ ] Bandwidth TURN di Tencent terukur.

---

## 11. Risiko & Mitigasi

| Risiko | Dampak | Mitigasi |
|---|---|---|
| Tunnel HTTP tidak bawa UDP | Meeting internet gagal | TURN + port-forward opsional |
| Cloudflare melihat metadata signaling (room/JWT) | Privasi | Media tetap DTLS-SRTP; room name acak; E2EE; jangan muat data sensitif di URL/JWT |
| Fitur moderasi harus dibangun | Waktu dev bertambah | Bangun bertahap: lobby, kick, mute dulu; raise hand/reaction belakangan |
| Kapasitas Pi 4B | Lag > ~10 peserta | Batasi per plan, simulcast/dynacast, LAN gigabit, fan |
| SD card rusak/clone | Downtime / kebocoran lisensi | SSD USB, backup config, token lisensi + heartbeat |
| Bandwidth Tencent (TURN) | Biaya naik | Monitoring, batas relay, utamakan direct/port-forward |
| Klaim "superadmin tidak bisa akses" tidak absolut | Kepercayaan | Support mode oleh Admin Client, OTA bertanda tangan + persetujuan, dokumentasi jujur |
| IP LAN berubah | Link lokal rusak | Agent update DNS otomatis, tampilkan IP di dashboard |
| Sertifikat kedaluwarsa saat offline lama | Peringatan browser | Auto-renew, monitoring; CA privat untuk air-gapped |
| mDNS/client isolation di Wi-Fi | Koneksi LAN gagal | Matikan client isolation, gunakan kabel untuk Pi |
| Perubahan versi LiveKit | Config break | Kunci tag versi, uji sebelum update |
| RAM Tencent 2 GB | Sesak bila banyak device | Tuning Postgres, swap, upgrade saat > ~30 device |

---

## 12. Estimasi Waktu (1 developer)

| Phase | Estimasi |
|---|---|
| 0 Persiapan | 1–2 hari |
| 1 Validasi LiveKit di Pi | 1–2 hari |
| 2 Control plane | 3–5 hari |
| 3 Device base | 3–4 hari |
| 4 Tunnel & domain | 2–3 hari |
| 5 Akses lokal HTTPS | 2 hari |
| 6 Dashboard Admin Client | 5–7 hari |
| 7 UI meeting + moderasi | 6–9 hari |
| 8 Lisensi | 2–3 hari |
| 9 Keamanan | 3–4 hari (paralel) |
| 10 Media internet | 2–3 hari |
| 11 Testing & packaging | 3–5 hari |
| **Total** | **± 5–7 minggu** |

---

## 13. Urutan Kerja yang Disarankan

1. **Hari 1–2:** LiveKit di Pi, uji 2–4 perangkat via LAN (Phase 1).
2. **Hari 3–5:** App token issuer minimal + UI join sederhana (React + components).
3. **Minggu 1–2:** Tunnel + domain per device + sertifikat + akses lokal.
4. **Minggu 2:** Control plane minimal (device + QR + heartbeat).
5. **Minggu 3:** Claim flow + dashboard Admin Client.
6. **Minggu 3–5:** UI custom + lobby + moderasi + TURN.
7. **Minggu 5–7:** Lisensi, hardening, load test, golden image, dokumentasi.

---

## 14. Keputusan Terbuka (Putuskan Sebelum Phase 4)

- [x] Tunnel: **Cloudflare Tunnel** (dashboard + signaling saja; media via TURN)
- [ ] TURN: bawaan LiveKit vs coturn?
- [ ] Penyimpanan Pi: SD card atau SSD USB?
- [ ] E2EE default on atau opsional?
- [ ] Batas peserta per plan?
- [ ] Heartbeat boleh kirim jumlah meeting agregat atau nol data?
- [ ] Support mode diperlukan?
- [ ] Penagihan: manual atau payment gateway?
- [ ] Fitur moderasi MVP: yang wajib vs bisa menyusul (raise hand/reaction)?
- [ ] Bahasa UI: Indonesia, Inggris, atau keduanya?
- [ ] Framework UI: React + LiveKit components atau JS murni?
