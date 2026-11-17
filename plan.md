# PLAN: Secure VIP Video Conference Appliance (Raspberry Pi 4B + Jitsi)

Dokumen kerja untuk membangun appliance video conference privat berbasis Raspberry Pi 4B, dengan control plane di Tencent Cloud.

---

## 1. Tujuan & Prinsip

**Tujuan:** Perangkat plug-and-play untuk meeting VIP/eksklusif. Admin Client cukup colok daya + LAN, scan QR, login, lalu mengelola user dan meeting sendiri.

**Prinsip desain:**
1. **Zero-touch deployment**: Pi nyala → service jalan otomatis → tunnel tersambung.
2. **Pemisahan data**: Superadmin hanya melihat metadata (status, kontrak, heartbeat), tidak pernah isi/akses meeting.
3. **Identitas hardware tetap**: Device ID + QR hanya diubah/dihapus oleh superadmin.
4. **Local-first**: Meeting lokal (LAN) tetap jalan tanpa internet.
5. **Secret tidak ada di QR**: QR hanya pointer ke device + kode klaim sekali pakai.

**Di luar scope (MVP):** recording (Jibri), transcoding, lebih dari ~10 peserta, secure element/TPM.

---

## 2. Arsitektur

```
                    ┌─────────────────────────────┐
                    │  TENCENT CLOUD (2c/2GB)     │
                    │  Control Plane              │
                    │  - Superadmin Web + API     │
                    │  - Postgres                 │
                    │  - Tunnel server (frps/WG)  │
                    │  - coturn (TURN, opsional)  │
                    │  - Caddy/Nginx reverse proxy│
                    └──────────┬──────────────────┘
                               │ tunnel (HTTP/WS + heartbeat)
        ┌──────────────────────┼──────────────────────┐
        │                      │                      │
 ┌──────▼──────┐        ┌──────▼──────┐        ┌──────▼──────┐
 │ Pi #1       │        │ Pi #2       │        │ Pi #N       │
 │ Data Plane  │        │ Data Plane  │        │ Data Plane  │
 │ Jitsi+App   │        │ Jitsi+App   │        │ Jitsi+App   │
 └─────────────┘        └─────────────┘        └─────────────┘
```

**Batas visibilitas Superadmin:**

| Data | Superadmin bisa lihat? |
|---|---|
| Plan, harga, kontrak, masa aktif | Ya |
| Status online/offline, versi, suhu CPU, disk | Ya |
| Jumlah meeting / durasi agregat (opsional) | Ya (metadata saja) |
| Nama user, isi chat, daftar peserta, room | **Tidak** |
| Audio/video | **Tidak** |
| Akses ke dashboard Admin Client | **Tidak** (tidak ada akun superadmin di Pi) |

---

## 3. Stack Teknologi

| Layer | Pilihan | Catatan |
|---|---|---|
| Control plane backend | FastAPI (Python) atau Django | Pilih yang paling Anda kuasai (Django cocok jika mau admin cepat) |
| Control plane DB | PostgreSQL 16 | Cukup di 2 GB RAM |
| Control plane frontend | HTMX/Alpine atau React ringan | Superadmin panel |
| Reverse proxy | Caddy | Auto TLS untuk `*.domainutama.com` (DNS-01) |
| Tunnel | **frp** atau **WireGuard** (utama), Cloudflare Tunnel (alternatif untuk web saja) | Lihat catatan media di bagian 7 |
| TURN | coturn | Untuk meeting via internet tanpa port-forward |
| Pi OS | Raspberry Pi OS Lite 64-bit (Bookworm) | Headless |
| Pi containers | Docker + Compose | Jitsi docker-jitsi-meet |
| Pi app backend | FastAPI/Django + SQLite atau Postgres kecil | Dashboard Admin Client |
| Pi UI meeting | Jitsi IFrame API + UI custom | JWT auth |
| Auth Jitsi | JWT (`prosody-jwt`) | Token dibuat oleh app lokal Pi |
| Update | Signed OTA (cosign/minisign) | Wajib persetujuan Admin Client |

---

## 4. Phase Rencana Kerja

### Phase 0: Persiapan (1–2 hari)
- [ ] Beli/siapkan domain utama (mis. `domainutama.com`) dan pindahkan DNS ke provider yang punya API (Cloudflare/DNSPod) untuk DNS-01.
- [ ] Siapkan Tencent Cloud 2c/2GB, buka port: 22 (restricted), 80, 443, 7000 (frp) / 51820/udp (WG), 3478 & 5349 (TURN) + range relay.
- [ ] Siapkan 1 Raspberry Pi 4B (4GB/8GB disarankan), SD card A2 / SSD USB, heatsink + fan, PSU 5V/3A resmi.
- [ ] Repo Git: `control-plane/`, `device/`, `deploy/`, `docs/`.

### Phase 1: Control Plane MVP (3–5 hari)
- [ ] Model data: `Plan`, `Client`, `Device`, `Contract`, `Heartbeat`, `AuditLog`, `SuperAdmin`.
- [ ] Auth superadmin (password + TOTP 2FA wajib).
- [ ] CRUD Plan (nama, harga, max peserta, max user, durasi).
- [ ] CRUD Client + Contract (mulai, berakhir, status: active/suspended/expired).
- [ ] Generate Device: `device_id` (ULID/UUID), `claim_code` sekali pakai, `device_secret` (hanya dikirim sekali saat provisioning ke Pi, disimpan hash di server).
- [ ] Generate QR (PNG/SVG + PDF label siap cetak) berisi:
  `https://<device_id>.domainutama.com/claim?c=<claim_code>`
- [ ] Endpoint heartbeat (`POST /api/v1/heartbeat`) dengan HMAC/mTLS menggunakan `device_secret`.
- [ ] Endpoint license check: mengembalikan token lisensi bertanda tangan (JWT EdDSA) dengan `exp` pendek (mis. 7 hari).
- [ ] Dashboard monitoring: online/offline, last seen, versi, suhu, disk, sisa kontrak.
- [ ] Audit log semua aksi superadmin.

### Phase 2: Device Base Image (3–4 hari)
- [ ] Install Raspberry Pi OS Lite 64-bit, hardening dasar (disable password SSH, ufw, unattended-upgrades terbatas).
- [ ] Install Docker + Compose.
- [ ] Docker Compose stack: `prosody`, `jicofo`, `jvb`, `web` (Jitsi), `app` (dashboard), `caddy` (TLS lokal), `agent` (tunnel+heartbeat).
- [ ] systemd unit `appliance.service` → `docker compose up -d` saat boot, restart policy `unless-stopped`.
- [ ] Watchdog hardware (`/dev/watchdog`) + health check container.
- [ ] Script **first-boot provisioning**: baca `/boot/firmware/device.json` (device_id + secret), simpan ke `/etc/appliance/`, lalu hapus dari boot partition.
- [ ] Log rotation + tmpfs untuk log berat untuk mengurangi wear SD card (atau pakai SSD).

### Phase 3: Tunnel & Domain (2–3 hari)
- [ ] Wildcard DNS `*.domainutama.com` → Tencent.
- [ ] Tunnel agent di Pi: koneksi keluar (outbound) ke Tencent, mapping `https://<device_id>.domainutama.com` → Pi:443 / app.
- [ ] Tunnel hanya meng-expose: dashboard app (HTTPS) dan signaling Jitsi (WSS). **Tidak** meng-expose SSH/admin.
- [ ] Reconnect otomatis + backoff.
- [ ] Verifikasi: Admin Client dari internet bisa buka `https://<device_id>.domainutama.com`.

### Phase 4: Akses Lokal (LAN) tanpa Peringatan Sertifikat (2 hari)
Kamera/mic browser wajib HTTPS (secure context).
- [ ] Opsi A (disarankan): DNS `<device_id>.local.domainutama.com` → IP LAN Pi (diatur agent saat IP berubah, via API DNS) + sertifikat Let's Encrypt DNS-01 di Pi.
- [ ] Opsi B: mDNS `device.local` + CA privat (butuh install CA di tiap klien, kurang praktis).
- [ ] Pi mengumumkan IP LAN ke dashboard sehingga Admin Client mendapat link lokal yang benar.
- [ ] Uji meeting lokal dengan internet WAN dicabut.

### Phase 5: App Dashboard Admin Client di Pi (5–7 hari)
- [ ] **Claim flow**: scan QR → halaman claim → verifikasi `claim_code` ke control plane → buat akun Admin pertama (set username/password) → claim_code hangus.
- [ ] Login (password + opsional TOTP), rate limit, lockout.
- [ ] Ganti password sendiri.
- [ ] CRUD user (role: admin, host, member/guest).
- [ ] Create meeting: judul, jadwal, room name acak (tidak mudah ditebak), passcode, lobby on/off, max peserta (sesuai plan), E2EE on/off.
- [ ] Join link: tamu internet (via domain tunnel) vs tamu lokal (link LAN).
- [ ] Riwayat meeting + **hapus history** (hard delete, bukan soft delete, plus vacuum SQLite).
- [ ] Pengaturan: nama organisasi, logo, warna tema.
- [ ] Halaman status: koneksi tunnel, status lisensi, suhu, versi, koneksi internet.
- [ ] API membuat **JWT Jitsi** per peserta (moderator vs tamu).

### Phase 6: UI Meeting Custom (4–6 hari)
- [ ] Embed Jitsi via **IFrame API** (lebih mudah di-upgrade) dengan toolbar custom, tema gelap/modern.
- [ ] Fitur standar: mute/unmute, kamera, share screen, chat, raise hand, reaction, tile/speaker view, daftar peserta, lobby (admit/deny), kick, mute all, layar penuh.
- [ ] Pre-join screen custom (cek mic/kamera, nama tampil).
- [ ] Responsive (desktop, tablet, mobile browser).
- [ ] Branding per client dari pengaturan.

### Phase 7: Lisensi, Kontrak, & Kill-Switch (2–3 hari)
- [ ] Agent mengambil license token berkala; disimpan terenkripsi lokal.
- [ ] Mode grace: bila Pi offline dari control plane, tetap jalan sampai `exp` token (mis. 7–14 hari), agar meeting lokal tidak mati hanya karena internet putus.
- [ ] Kontrak habis/suspend → app menolak pembuatan meeting baru (dan setelah grace, nonaktif). Pesan jelas ke Admin Client.
- [ ] Batasan plan dipaksa di app (max user, max peserta, fitur).
- [ ] Catatan: ini **pencegah**, bukan DRM mutlak.

### Phase 8: Keamanan & Privasi (3–4 hari, paralel)
- [ ] Tidak ada akun/SSH superadmin di Pi pada kondisi normal. Akses support hanya via **support mode** yang diaktifkan Admin Client (token sementara, auto-expire, tercatat di log).
- [ ] Heartbeat hanya berisi metadata (tidak ada nama user/room/peserta).
- [ ] E2EE Jitsi tersedia sebagai opsi (browser Chromium-based), dengan catatan keterbatasannya.
- [ ] OTA update bertanda tangan; Admin Client menyetujui sebelum diterapkan; changelog transparan.
- [ ] Enkripsi data at-rest (LUKS untuk partisi data, atau minimal field-level encryption untuk data sensitif).
- [ ] Backup/export terenkripsi opsional untuk Admin Client.
- [ ] Hardening: fail2ban, header keamanan, CSP, CSRF, cookie `HttpOnly/Secure/SameSite`.
- [ ] Review: pastikan tunnel tidak membuka port/jalur selain yang direncanakan.

### Phase 9: Media untuk Meeting Internet (2–3 hari)
**Penting:** Tunnel HTTP (mis. Cloudflare Tunnel) **tidak** membawa media UDP JVB.
- [ ] Opsi 1: Port-forward UDP 10000 di router klien (jika klien mampu).
- [ ] Opsi 2: **coturn di Tencent** (TURN UDP/TCP/TLS 443) → peserta dari NAT/firewall ketat tetap bisa terhubung. Media tetap terenkripsi DTLS-SRTP.
- [ ] Opsi 3: JVB TCP fallback 4443 via tunnel TCP (frp) sebagai cadangan, kualitas lebih rendah.
- [ ] Konfigurasi JVB: `harvest` dengan IP publik/relay yang benar.
- [ ] Uji dengan peserta di jaringan berbeda (4G, kantor dengan firewall).
- [ ] Hitung bandwidth Tencent bila TURN dipakai (relay memakan bandwidth).

### Phase 10: Testing, Packaging, Serah Terima (3–5 hari)
- [ ] Skenario uji end-to-end (bagian 8).
- [ ] Load test: 4, 6, 8, 10 peserta, pantau CPU/suhu/bandwidth Pi.
- [ ] Buat **golden image** (`.img`) + skrip provisioning massal (flash → tulis `device.json`).
- [ ] Cetak label QR + sticker Device ID di casing.
- [ ] Dokumen user (PDF singkat): 6 langkah serah terima.
- [ ] Runbook superadmin: provisioning, suspend, ganti QR, RMA.

---

## 5. Skema Database

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

### Device (SQLite/Postgres lokal)

```sql
org_settings(id, org_name, logo_path, theme jsonb)
users(id, username UNIQUE, display_name, password_hash, role, totp_secret NULL,
      active, created_at, last_login)
meetings(id, title, room_name UNIQUE, passcode_hash NULL, host_user_id,
         scheduled_at, lobby bool, e2ee bool, max_participants, created_at, ended_at)
meeting_events(id, meeting_id, ts, type, detail)   -- minimal; bisa dimatikan
sessions(id, user_id, token_hash, expires_at, ip)
license_state(id, token_encrypted, expires_at, last_checked)
local_audit(id, user_id, action, ts)
```

---

## 6. Struktur Repo

```
vip-vc/
├── plan.md
├── control-plane/
│   ├── app/            # API + superadmin UI
│   ├── migrations/
│   ├── docker-compose.yml
│   └── caddy/Caddyfile
├── device/
│   ├── app/            # dashboard Admin Client
│   ├── agent/          # tunnel + heartbeat + license
│   ├── jitsi/          # config custom (.env, jwt)
│   ├── docker-compose.yml
│   ├── systemd/appliance.service
│   └── provisioning/   # first-boot scripts
├── image-builder/      # build golden image
├── docs/
│   ├── handover-guide.md
│   └── runbook.md
└── scripts/
```

---

## 7. Alur Penting

### 7.1 Provisioning (Superadmin)
1. Superadmin buat Device → sistem generate `device_id`, `device_secret`, `claim_code`.
2. Sistem keluarkan `device.json` + QR PNG/PDF.
3. Tulis `device.json` ke boot partition SD card (skrip), flash image.
4. Tempel QR di casing.
5. Status device: `provisioned`.

### 7.2 Serah Terima (Admin Client)
1. Nyalakan Pi.
2. Colok kabel LAN.
3. Pi boot → compose up → tunnel tersambung → heartbeat.
4. Scan QR → buka `https://<device_id>.domainutama.com/claim?c=...`
5. Buat akun Admin (claim code hangus, status `claimed`).
6. Login → dashboard → ganti password, buat user, buat meeting, kelola history.

### 7.3 Meeting
- **Lokal:** link `https://<device_id>.local.domainutama.com/m/<room>` → JWT lokal → Jitsi LAN.
- **Internet:** link `https://<device_id>.domainutama.com/m/<room>` → signaling via tunnel, media via port-forward/TURN.

---

## 8. Checklist Pengujian

**Fungsional**
- [ ] Boot dari dingin → semua service naik otomatis < 2 menit.
- [ ] Reboot mendadak (cabut daya) → pulih tanpa korupsi data.
- [ ] Claim flow: claim_code tidak bisa dipakai dua kali.
- [ ] QR lama tidak berfungsi setelah superadmin regenerate.
- [ ] CRUD user, meeting, hapus history (data benar-benar hilang).
- [ ] Meeting lokal dengan internet dicabut.
- [ ] Meeting internet dari 4G / jaringan lain.
- [ ] Mixed: sebagian lokal, sebagian internet.

**Keamanan & privasi**
- [ ] Superadmin tidak punya jalur login ke dashboard Pi.
- [ ] Heartbeat tidak memuat data user/room.
- [ ] Port scan dari luar: hanya yang direncanakan terbuka.
- [ ] JWT kedaluwarsa dan tidak bisa dipakai ulang.
- [ ] Lisensi expired → perilaku sesuai rencana.
- [ ] Tanpa E2EE vs dengan E2EE berfungsi.

**Performa**
- [ ] 4 / 6 / 8 / 10 peserta: CPU, suhu, packet loss, kualitas video.
- [ ] Suhu stabil < 75°C dengan casing + fan.
- [ ] Bandwidth TURN di Tencent terukur.

---

## 9. Risiko & Mitigasi

| Risiko | Dampak | Mitigasi |
|---|---|---|
| Tunnel tidak bawa UDP | Meeting internet gagal | TURN di Tencent + port-forward opsional |
| Kapasitas Pi 4B | Lag > ~10 peserta | Batasi di plan, simulcast, LAN gigabit, fan |
| SD card rusak/clone | Downtime / kebocoran lisensi | SSD USB, backup config, token lisensi + heartbeat |
| Bandwidth Tencent habis (TURN) | Biaya naik | Monitoring, batas relay, prioritas direct/port-forward |
| Klaim "superadmin tidak bisa akses" tidak absolut | Reputasi/kepercayaan | Support mode oleh Admin Client, OTA bertanda tangan + persetujuan, dokumentasi jujur |
| IP LAN berubah | Link lokal rusak | Agent update DNS otomatis, tampilkan IP di dashboard |
| Cert expired | Peringatan browser | Auto-renew + monitoring |
| 2 GB RAM Tencent | Sesak bila klien banyak | Postgres tuning, swap, batasi coturn, upgrade saat > ~30 device |

---

## 10. Estimasi Waktu (1 developer)

| Phase | Estimasi |
|---|---|
| 0 Persiapan | 1–2 hari |
| 1 Control plane | 3–5 hari |
| 2 Device base | 3–4 hari |
| 3 Tunnel & domain | 2–3 hari |
| 4 Akses lokal | 2 hari |
| 5 App dashboard | 5–7 hari |
| 6 UI meeting | 4–6 hari |
| 7 Lisensi | 2–3 hari |
| 8 Keamanan | 3–4 hari (paralel) |
| 9 Media internet | 2–3 hari |
| 10 Testing & packaging | 3–5 hari |
| **Total** | **± 4–6 minggu** |

---

## 11. Urutan Kerja yang Disarankan (Mulai dari Sini)

1. **Hari 1–2:** Jalankan Jitsi docker di Pi, uji meeting lokal 2 perangkat. Ini memvalidasi hardware.
2. **Hari 3–4:** Tambah JWT auth + IFrame API sederhana.
3. **Hari 5–7:** Tunnel ke Tencent + domain per-device + sertifikat.
4. **Minggu 2:** Control plane minimal (device + QR + heartbeat).
5. **Minggu 2–3:** Claim flow + dashboard Admin Client.
6. **Minggu 3–4:** UI custom, TURN, lisensi.
7. **Minggu 5–6:** Hardening, load test, golden image, dokumentasi.

---

## 12. Keputusan Terbuka (Putuskan Sebelum Phase 3)

- [ ] Tunnel: frp vs WireGuard vs Cloudflare Tunnel (+ TURN)?
- [ ] Penyimpanan: SD card saja atau SSD USB?
- [ ] E2EE default on atau opsional?
- [ ] Jumlah peserta maksimum per plan?
- [ ] Heartbeat boleh kirim jumlah meeting agregat atau nol data?
- [ ] Support mode: apakah diperlukan?
- [ ] Penagihan: manual atau terintegrasi payment gateway?
- [ ] Bahasa UI: Indonesia, Inggris, atau keduanya?
