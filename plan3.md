# PLAN v3: Secure VIP Video Conference Appliance (Raspberry Pi 4B + LiveKit)

Revisi dari plan2.md. Perubahan utama:
1. **Dua mode konektivitas**: *Standard* (Cloudflare Tunnel) dan *Sovereign* (WireGuard/frp tanpa Cloudflare).
2. **Link lokal sebagai fitur inti**: meeting tetap jalan tanpa internet, lengkap dengan DNS lokal (dnsmasq) dan HTTPS.
3. **Dashboard menghasilkan dua jenis link** per meeting: lokal dan internet.

---

## 0. Ringkasan Perubahan

| Area | plan2.md | plan3.md |
|---|---|---|
| Tunnel | frp/WireGuard (pilih satu) | **Dua backend**: Cloudflare Tunnel (Standard) atau WireGuard/frp (Sovereign), dipilih per device |
| DNS | DNS provider generik | **Cloudflare DNS API** (CNAME tunnel + record LAN + DNS-01) |
| Link meeting | Lokal dan internet disebut singkat | **Dua link eksplisit** di dashboard, dengan tombol salin |
| Offline | HTTPS lokal | + **dnsmasq di Pi**, mDNS cadangan, strategi sertifikat offline |
| QR | Satu URL internet | QR internet + **alamat lokal cadangan di label** |
| Plan/harga | Satu produk | Bisa dijual sebagai **Standard** dan **Sovereign** |
| Peran Tencent | Control plane + tunnel server + TURN | Control plane + TURN (+ tunnel server hanya untuk Sovereign) |

Tidak berubah: LiveKit sebagai media server, claim flow QR, lisensi, pemisahan data superadmin vs Admin Client.

---

## 1. Tujuan & Prinsip

**Tujuan:** Perangkat plug-and-play untuk meeting VIP/eksklusif. Admin Client colok daya + LAN, scan QR, login, lalu mengelola user dan meeting sendiri.

**Prinsip:**
1. **Zero-touch deployment**: Pi nyala → service jalan otomatis → koneksi ke pusat tersambung.
2. **Pemisahan data**: Superadmin hanya melihat metadata, tidak pernah isi/akses meeting.
3. **Identitas hardware tetap**: Device ID + QR hanya diubah/dihapus superadmin.
4. **Local-first**: Meeting LAN jalan penuh tanpa internet dan tanpa Cloudflare.
5. **Secret tidak ada di QR**: QR hanya berisi pointer + claim code sekali pakai.
6. **Jujur soal batas privasi**: peran Cloudflare dan batas kemampuan superadmin didokumentasikan apa adanya.

**Di luar scope MVP:** recording/egress (terlalu berat untuk Pi), transcoding, lebih dari ~10 peserta, secure element/TPM.

---

## 2. Arsitektur

```
                ┌──────────────────────────────────────┐
                │  TENCENT CLOUD (2c / 2GB)            │
                │  Control Plane                       │
                │  - Superadmin Web + API              │
                │  - Postgres                          │
                │  - TURN (livekit-turn / coturn)      │
                │  - WireGuard/frp server (Sovereign)  │
                └───────┬──────────────────┬───────────┘
                        │ API              │ WG/frp (Sovereign saja)
                ┌───────▼────────┐         │
                │ CLOUDFLARE     │         │
                │ DNS + Tunnel   │         │
                │ (Standard)     │         │
                └───────┬────────┘         │
                        │ cloudflared      │
       ┌────────────────▼──────────────────▼───────┐
       │  Raspberry Pi (per klien)                 │
       │  livekit · app · caddy · agent · dnsmasq  │
       └───────────────┬───────────────────────────┘
                       │ LAN (tanpa internet pun jalan)
                  Peserta lokal
```

**Dua jalur akses ke Pi**

| Jalur | Lewat | Perlu internet? | Dipakai untuk |
|---|---|---|---|
| Lokal | LAN langsung | Tidak | Peserta di ruangan/kantor yang sama |
| Internet (Standard) | Cloudflare Tunnel + TURN | Ya | Peserta jarak jauh |
| Internet (Sovereign) | WireGuard/frp ke Tencent + TURN | Ya | Peserta jarak jauh, tanpa pihak ketiga |

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

## 3. Dua Mode Konektivitas

| Aspek | **Standard** | **Sovereign** |
|---|---|---|
| Tunnel | Cloudflare Tunnel (`cloudflared`) | WireGuard atau frp ke Tencent |
| Pihak ketiga melihat login/signaling? | Cloudflare men-terminate TLS, **secara teknis ya** | **Tidak** (TLS selesai di Pi/Tencent Anda) |
| Media (audio/video) | Tidak lewat tunnel, terenkripsi DTLS-SRTP | Sama |
| Setup | Otomatis via API Cloudflare | Perlu kelola server tunnel di Tencent |
| Proteksi DDoS | Ada (Cloudflare) | Terbatas |
| E2EE LiveKit | Opsional | **Aktif default** |
| Cocok untuk | Perusahaan, hospitality, umum | Hukum, pemerintah, militer, sangat sensitif |
| Titik gagal tunggal | Akun Cloudflare | Server Tencent |
| Harga | Plan standar | Plan premium |

**Implementasi:** agent di Pi memiliki dua backend tunnel; dipilih lewat field `connectivity_mode` di `device.json`. Satu golden image untuk keduanya.

**Catatan kepatuhan:** periksa Terms of Service Cloudflare terkait penggunaan Tunnel untuk dashboard/signaling WebSocket. Media tidak lewat tunnel, jadi risikonya kecil, tapi pastikan di versi terbaru.

---

## 4. Stack Teknologi

| Layer | Pilihan | Catatan |
|---|---|---|
| Control plane backend | FastAPI atau Django | Pilih yang paling dikuasai |
| Control plane DB | PostgreSQL 16 | Cukup di 2 GB RAM |
| Control plane UI | HTMX/Alpine atau React ringan | Panel superadmin |
| DNS & tunnel (Standard) | Cloudflare (DNS API + Tunnel API) | Token API sempit: Zone DNS Edit + Tunnel |
| Tunnel (Sovereign) | WireGuard (disarankan) atau frp | Server di Tencent |
| TURN | TURN bawaan LiveKit atau coturn | Di Tencent |
| OS Pi | Raspberry Pi OS Lite 64-bit (Bookworm) | Headless |
| Container | Docker + Compose | |
| Media server | **livekit-server** (ARM64) | Satu container |
| Reverse proxy Pi | Caddy | TLS lokal dan internet |
| DNS lokal Pi | **dnsmasq** | Menjawab nama lokal saat offline |
| Pi app backend | FastAPI/Django + SQLite | Dashboard + token issuer + moderasi |
| Pi UI | React + `@livekit/components-react` | UI modern custom |
| Update | OTA bertanda tangan (cosign/minisign) | Wajib persetujuan Admin Client |

---

## 5. Phase Rencana Kerja

### Phase 0: Persiapan (1–2 hari)
- [ ] Domain utama dengan DNS di Cloudflare.
- [ ] Buat API token Cloudflare dengan izin sempit (Zone DNS Edit untuk zona terkait, Tunnel Edit).
- [ ] Tencent Cloud 2c/2GB: buka port 80/443, 3478 & 5349 (TURN) + range relay, 51820/udp (WireGuard); SSH dibatasi.
- [ ] 1 Raspberry Pi 4B (4/8 GB), SSD USB atau SD A2, heatsink + fan, PSU 5V/3A.
- [ ] Repo Git: `control-plane/`, `device/`, `deploy/`, `docs/`.

### Phase 1: Validasi LiveKit di Pi (1–2 hari) ← MULAI DI SINI
- [ ] Jalankan livekit-server di Pi (compose bagian 6).
- [ ] Buat token uji, join dari 2 perangkat via LAN.
- [ ] Ukur CPU/suhu pada 2, 4, 6, 8 peserta (kamera 720p).
- [ ] Tentukan batas peserta realistis per plan.

### Phase 2: Akses Lokal Penuh (3–4 hari)
Inti produk: meeting jalan tanpa internet.
- [ ] Caddy di Pi melayani dashboard + signaling LiveKit lewat HTTPS.
- [ ] **dnsmasq** di Pi menjawab `<device_id>.local.domainutama.com` → IP LAN Pi.
- [ ] Opsi distribusi DNS lokal (pilih per klien): (a) router/DHCP klien diarahkan ke Pi sebagai DNS, (b) mDNS cadangan `namadevice.local`, (c) akses lewat IP langsung.
- [ ] Strategi sertifikat (bagian 8).
- [ ] Dashboard menampilkan IP LAN, nama lokal, dan status sertifikat.
- [ ] Uji penuh: WAN dicabut, 4+ peserta join lewat link lokal.

### Phase 3: Control Plane MVP (3–5 hari)
- [ ] Model: `Plan`, `Client`, `Device`, `Contract`, `Heartbeat`, `AuditLog`, `SuperAdmin`.
- [ ] Auth superadmin (password + TOTP 2FA wajib).
- [ ] CRUD Plan (nama, harga, max peserta, max user, durasi, `connectivity_mode`).
- [ ] CRUD Client + Contract.
- [ ] Generate Device: `device_id`, `claim_code` sekali pakai, `device_secret` (di server hanya hash).
- [ ] **Provisioning Cloudflare otomatis** (Standard): buat tunnel via API, buat CNAME `<device_id>.domainutama.com`, simpan token tunnel ke `device.json`.
- [ ] Provisioning Sovereign: buat peer WireGuard + config.
- [ ] Generate QR dan label cetak (bagian 9).
- [ ] Heartbeat (`POST /api/v1/heartbeat`) dengan HMAC/mTLS.
- [ ] Endpoint license: JWT (EdDSA) dengan `exp` pendek.
- [ ] Dashboard monitoring: online/offline, last seen, versi, suhu, disk, kontrak, mode konektivitas.
- [ ] Audit log semua aksi superadmin.

### Phase 4: Device Base Image (3–4 hari)
- [ ] Raspberry Pi OS Lite 64-bit, hardening dasar (SSH key only, ufw, unattended-upgrades terbatas).
- [ ] Docker + Compose.
- [ ] Stack: `livekit`, `app`, `caddy`, `agent`, `dnsmasq`.
- [ ] systemd `appliance.service`; restart `unless-stopped`.
- [ ] Hardware watchdog + health check.
- [ ] First-boot provisioning: baca `/boot/firmware/device.json`, simpan ke `/etc/appliance/`, hapus dari boot partition; generate API key/secret LiveKit lokal.
- [ ] Log rotation + tmpfs untuk log berat.

### Phase 5: Tunnel & Domain Internet (2–3 hari)
- [ ] Agent mendukung dua backend: `cloudflared` (Standard) dan WireGuard/frp (Sovereign).
- [ ] Hostname `https://<device_id>.domainutama.com` → Pi (dashboard + WSS signaling LiveKit).
- [ ] Tunnel **tidak** meng-expose SSH/admin.
- [ ] Reconnect otomatis + backoff.
- [ ] Agent memperbarui record LAN `<device_id>.local.domainutama.com` (DNS-only, bukan proxied) via API saat IP berubah.
- [ ] Uji kedua mode dari internet.

### Phase 6: Dashboard Admin Client di Pi (5–7 hari)
- [ ] **Claim flow**: scan QR → verifikasi `claim_code` → buat akun Admin pertama → claim_code hangus.
- [ ] Login (password + opsional TOTP), rate limit, lockout.
- [ ] Ganti password sendiri.
- [ ] CRUD user (role: admin, host, member, guest).
- [ ] Create meeting: judul, jadwal, room acak, passcode, lobby, max peserta, E2EE on/off.
- [ ] **Dua link per meeting** (bagian 7): Link Lokal dan Link Internet, masing-masing dengan tombol salin dan QR kecil.
- [ ] Riwayat meeting + **hapus history** (hard delete + vacuum).
- [ ] Pengaturan organisasi: nama, logo, tema.
- [ ] Halaman status: tunnel, lisensi, suhu, versi, internet, sertifikat, mode konektivitas.
- [ ] **Token issuer** LiveKit per peserta sesuai role.

### Phase 7: UI Meeting Custom + Moderasi (6–9 hari)
- [ ] Pre-join (cek mic/kamera, pilih perangkat, nama tampil).
- [ ] Room UI: grid/speaker view, mic/kamera/share screen, chat, daftar peserta.
- [ ] Lobby, kick, mute, mute all, promote host, lock room, raise hand, reaction (bagian 10).
- [ ] Responsif (desktop, tablet, mobile browser).
- [ ] Branding per client.
- [ ] Indikator koneksi, reconnect, adaptive stream/dynacast.

### Phase 8: Lisensi, Kontrak, Kill-Switch (2–3 hari)
- [ ] Agent ambil license token berkala; simpan terenkripsi.
- [ ] Mode grace: offline dari control plane tetap jalan sampai `exp` token (7–14 hari). **Meeting lokal tidak boleh mati hanya karena internet putus.**
- [ ] Kontrak habis/suspend → tolak meeting baru, lalu nonaktif setelah grace. Pesan jelas ke Admin Client.
- [ ] Batas plan dipaksa di app.
- [ ] Ini **pencegah**, bukan DRM mutlak.

### Phase 9: Keamanan & Privasi (3–4 hari, paralel)
- [ ] Tidak ada akun/SSH superadmin di Pi. Akses support hanya via **support mode** yang diaktifkan Admin Client (token sementara, auto-expire, tercatat).
- [ ] Heartbeat hanya metadata.
- [ ] E2EE LiveKit (shared key); kunci dibagikan lewat link/passcode, bukan lewat server pusat. Default aktif di Sovereign.
- [ ] OTA bertanda tangan + persetujuan Admin Client + changelog.
- [ ] Enkripsi data at-rest (LUKS partisi data atau field-level).
- [ ] Hardening web: CSP, CSRF, cookie `HttpOnly/Secure/SameSite`, fail2ban.
- [ ] Token Cloudflare/DNS yang ada di Pi dibatasi seminimal mungkin (lihat risiko).
- [ ] Verifikasi tunnel tidak membuka jalur di luar rencana.
- [ ] API key/secret LiveKit hanya di Pi.

### Phase 10: Media untuk Meeting Internet (2–3 hari)
Tunnel HTTP (termasuk Cloudflare) **tidak** membawa media UDP.
- [ ] Opsi 1: port-forward UDP 7882 + TCP 7881 di router klien.
- [ ] Opsi 2: **TURN** (TLS 443/5349) di Tencent. Media tetap terenkripsi DTLS-SRTP.
- [ ] Opsi 3: fallback TCP 7881 lewat tunnel TCP (hanya Sovereign/frp); kualitas lebih rendah.
- [ ] Opsional: evaluasi layanan TURN terkelola Cloudflare (cek ketersediaan dan tarif terbaru).
- [ ] Atur `rtc.node_ip` / external IP sesuai skenario.
- [ ] Uji dari 4G dan jaringan kantor berfirewall.
- [ ] Pantau bandwidth Tencent bila TURN dipakai.

### Phase 11: Testing, Packaging, Serah Terima (3–5 hari)
- [ ] Skenario uji end-to-end (bagian 12).
- [ ] Load test 4/6/8/10 peserta.
- [ ] **Golden image** (`.img`) + skrip provisioning massal.
- [ ] Cetak label QR + alamat lokal cadangan.
- [ ] Panduan Admin Client (PDF 1 halaman, 6 langkah).
- [ ] Runbook superadmin: provisioning, suspend, ganti QR, RMA, ganti mode konektivitas.
- [ ] Dokumen "Apa yang bisa dan tidak bisa dilihat pihak lain" untuk klien.

---

## 6. Konfigurasi di Pi

### 6.1 `livekit.yaml`

```yaml
port: 7880
bind_addresses:
  - ""
rtc:
  tcp_port: 7881
  udp_port: 7882
  use_external_ip: false      # tanpa STUN, cocok untuk LAN/offline
  node_ip: 192.168.1.50       # diisi otomatis oleh agent dari IP LAN Pi
keys:
  APIKEY_DIISI_SAAT_FIRST_BOOT: SECRET_DIISI_SAAT_FIRST_BOOT
logging:
  level: info
room:
  empty_timeout: 300
  max_participants: 10        # ikut batas plan, diset oleh app
```

> Nama field dan perilaku bisa berubah antar versi LiveKit. Cek dokumentasi versi yang dipakai (terutama `rtc` dan TURN) sebelum finalisasi. Kunci tag versi di produksi.

### 6.2 `docker-compose.yml`

```yaml
services:
  livekit:
    image: livekit/livekit-server:latest   # kunci ke tag versi tertentu
    command: --config /etc/livekit.yaml
    network_mode: host
    restart: unless-stopped
    volumes:
      - ./livekit.yaml:/etc/livekit.yaml:ro

  app:
    build: ./app
    restart: unless-stopped
    network_mode: host
    environment:
      LIVEKIT_URL: ws://127.0.0.1:7880
      LIVEKIT_API_KEY_FILE: /etc/appliance/livekit_key
      LIVEKIT_API_SECRET_FILE: /etc/appliance/livekit_secret
    volumes:
      - appdata:/data
      - /etc/appliance:/etc/appliance:ro

  caddy:
    image: caddy:2                          # butuh build dengan plugin DNS Cloudflare untuk DNS-01
    restart: unless-stopped
    network_mode: host
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddydata:/data

  dnsmasq:
    image: <image-dnsmasq-arm64>
    restart: unless-stopped
    network_mode: host
    cap_add: [NET_ADMIN]
    volumes:
      - ./dnsmasq.conf:/etc/dnsmasq.conf:ro

  agent:
    build: ./agent    # tunnel (cloudflared/WG) + heartbeat + license + DNS updater
    restart: unless-stopped
    network_mode: host
    volumes:
      - /etc/appliance:/etc/appliance

volumes:
  appdata:
  caddydata:
```

### 6.3 `dnsmasq.conf` (sketsa)

```
# Jawab nama lokal dengan IP LAN Pi (diisi otomatis oleh agent)
address=/<device_id>.local.domainutama.com/192.168.1.50
# Forward sisanya ke resolver upstream bila ada internet
server=1.1.1.1
no-resolv
```

### 6.4 Caddyfile (sketsa)

```
<device_id>.domainutama.com, <device_id>.local.domainutama.com {
    tls {
        dns cloudflare {env.CF_DNS_TOKEN}
    }
    handle /rtc* {
        reverse_proxy 127.0.0.1:7880    # WSS signaling LiveKit
    }
    handle {
        reverse_proxy 127.0.0.1:8000    # dashboard/API
    }
}
```

> Path signaling LiveKit bisa berbeda per versi/SDK. Uji dan sesuaikan. Pada mode Standard, host internet dilayani Cloudflare Tunnel; pada Sovereign, Caddy di Pi/Tencent melayani langsung.

---

## 7. Dua Jenis Link Meeting

Dashboard Admin Client membuat dua link untuk setiap meeting:

| | Link Lokal | Link Internet |
|---|---|---|
| Contoh | `https://<device_id>.local.domainutama.com/m/<room>` | `https://<device_id>.domainutama.com/m/<room>` |
| Lewat | LAN langsung | Cloudflare Tunnel (Standard) / WG (Sovereign) |
| Butuh internet | **Tidak** | Ya |
| Latensi | Terendah | Tergantung jaringan |
| Media | Langsung ke Pi | Port-forward atau TURN |

**Alur join (kedua jenis):** peserta buka link → app verifikasi passcode/lobby → app menerbitkan token LiveKit → peserta masuk. Peserta lokal dan internet bisa berada di **room yang sama**.

**Aturan UI:**
- Tampilkan kedua link berdampingan dengan tombol **Salin** dan **QR**.
- Jika tidak ada internet, tandai Link Internet "tidak tersedia" dan sorot Link Lokal.
- Jika DNS lokal belum dikonfigurasi di jaringan klien, tampilkan juga alamat cadangan berbasis IP dan mDNS dengan catatan peringatan sertifikat.

---

## 8. Strategi HTTPS & Sertifikat (Lokal)

Browser mewajibkan HTTPS untuk kamera/mic.

| Opsi | Peringatan browser | Offline lama | Catatan |
|---|---|---|---|
| **A. Let's Encrypt DNS-01** untuk `<device_id>.local.domainutama.com` | Tidak ada | Perlu internet sesekali (±60 hari) untuk perpanjang | **Default.** Tidak perlu pasang apa pun di perangkat peserta |
| **B. CA privat** | Tidak ada setelah CA dipasang | Sertifikat berumur panjang | Untuk lingkungan benar-benar air-gapped. CA harus dipasang di tiap perangkat peserta |
| **C. Self-signed** | Ada, harus diklik lanjut | Aman | Kesan kurang profesional; hanya cadangan |

**Catatan teknis:**
- Let's Encrypt tidak menerbitkan sertifikat untuk IP privat atau nama `.local` (mDNS). Karena itu nama lokal memakai **subdomain dari domain publik Anda** yang di-resolve ke IP LAN oleh dnsmasq.
- Akses lewat IP langsung atau `namadevice.local` akan memunculkan peringatan sertifikat kecuali memakai opsi B.
- Mode klien: tawarkan **A sebagai default**, **B sebagai opsi air-gapped**.
- Dashboard menampilkan tanggal kedaluwarsa sertifikat dan memperingatkan Admin Client bila perpanjangan gagal (mis. Pi lama offline).
- Penyebaran DNS lokal: agar nama lokal ter-resolve, perangkat peserta harus memakai Pi sebagai DNS (lewat pengaturan DHCP/router klien) atau menggunakan DNS yang meneruskan ke Pi. Jelaskan ini di panduan serah terima.

---

## 9. QR & Label Hardware

- **QR (claim):** `https://<device_id>.domainutama.com/claim?c=<claim_code>` (butuh internet untuk klaim pertama).
- **Label cetak** di casing:
  - Device ID
  - QR di atas
  - Alamat lokal cadangan: `https://<device_id>.local.domainutama.com`
  - Nama mDNS: `<nama-device>.local`
- **Klaim tanpa internet:** jika klaim pertama dilakukan tanpa internet, sediakan jalur alternatif: halaman klaim lokal yang memverifikasi `claim_code` dan `device_secret` secara lokal, lalu mensinkronkan status ke control plane saat internet tersedia. (Putuskan di Keputusan Terbuka.)
- QR tidak mengandung secret; `claim_code` hangus setelah dipakai atau saat superadmin regenerate.

---

## 10. Fitur Meeting: Bawaan vs Dibangun Sendiri

| Fitur | Status di LiveKit | Cara implementasi |
|---|---|---|
| Audio/video, mute, kamera | Bawaan | SDK |
| Share screen | Bawaan | SDK |
| Chat | Bawaan (komponen) / data channel | SDK / `publishData` |
| Daftar peserta, speaker aktif | Bawaan | SDK |
| Adaptive stream, simulcast, dynacast | Bawaan | Room options |
| E2EE | Didukung | Shared key |
| **Lobby / waiting room** | Tidak ada | App menahan token; host approve → token diterbitkan |
| **Raise hand / reaction** | Tidak ada | Data channel / participant metadata |
| **Kick peserta** | Via server API | `RemoveParticipant` |
| **Mute peserta / mute all** | Via server API | `MutePublishedTrack` |
| **Promote/demote host** | Via server API | `UpdateParticipant` |
| **Kunci room / passcode** | Dibangun | Tolak token baru / verifikasi di app |
| **Recording** | Butuh Egress (berat) | Di luar scope Pi 4B |

**Pola auth:** peserta tidak pernah memegang API secret. App Pi menerbitkan token berumur pendek dengan grants minimal sesuai role.

---

## 11. Skema Database

### Control Plane (Postgres)

```sql
plans(id, name, price_monthly, price_yearly, max_participants, max_users,
      connectivity_mode ENUM('standard','sovereign'),
      features jsonb, active)

clients(id, name, contact_email, contact_phone, notes, created_at)

devices(id, device_id UNIQUE, client_id NULL, plan_id NULL,
        secret_hash, claim_code_hash, claimed_at NULL,
        connectivity_mode ENUM('standard','sovereign'),
        cf_tunnel_id NULL, cf_dns_record_id NULL, wg_peer_id NULL,
        status ENUM('inventory','provisioned','claimed','suspended','revoked'),
        qr_version INT, created_at)

contracts(id, client_id, device_id, plan_id, start_date, end_date,
          status ENUM('active','suspended','expired'), price_agreed)

heartbeats(id, device_id, ts, app_version, cpu_temp, disk_pct, mem_pct,
           uptime, lan_ip, tunnel_ok bool, cert_expires_at,
           meetings_count_24h INT)

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
lobby_requests(id, meeting_id, display_name, requested_at, status)  -- sementara
meeting_events(id, meeting_id, ts, type, detail)                    -- minimal; bisa dimatikan
sessions(id, user_id, token_hash, expires_at, ip)
license_state(id, token_encrypted, expires_at, last_checked)
network_state(id, lan_ip, local_hostname, cert_expires_at, dns_mode, updated_at)
local_audit(id, user_id, action, ts)
```

---

## 12. Alur Penting

### 12.1 Provisioning (Superadmin)
1. Pilih Plan (Standard/Sovereign) → buat Device → generate `device_id`, `device_secret`, `claim_code`.
2. Standard: buat tunnel + CNAME via API Cloudflare. Sovereign: buat peer WireGuard.
3. Keluarkan `device.json` + QR + label cetak.
4. Tulis `device.json` ke boot partition SD, flash image.
5. Tempel label di casing. Status: `provisioned`.
6. First boot: generate API key/secret LiveKit lokal.

### 12.2 Serah Terima (Admin Client)
1. Nyalakan Pi → 2. colok LAN → 3. service naik otomatis → 4. scan QR → 5. buat akun Admin (claim_code hangus) → 6. login, ganti password, buat user/meeting, kelola history.

### 12.3 Join Meeting
Lihat bagian 7 (dua jenis link).

---

## 13. Struktur Repo

```
vip-vc/
├── plan3.md
├── control-plane/
│   ├── app/
│   │   └── integrations/   # cloudflare.py, wireguard.py
│   ├── migrations/
│   └── docker-compose.yml
├── device/
│   ├── app/                # dashboard + token issuer + moderasi API
│   ├── web/                # UI meeting (React + LiveKit components)
│   ├── agent/              # tunnel backends, heartbeat, license, DNS updater
│   ├── livekit/livekit.yaml.tpl
│   ├── dnsmasq/dnsmasq.conf.tpl
│   ├── Caddyfile.tpl
│   ├── docker-compose.yml
│   ├── systemd/appliance.service
│   └── provisioning/
├── image-builder/
├── docs/
│   ├── handover-guide.md
│   ├── privacy-disclosure.md
│   └── runbook.md
└── scripts/
```

---

## 14. Checklist Pengujian

**Fungsional**
- [ ] Boot dingin → semua service naik < 2 menit.
- [ ] Cabut daya mendadak → pulih tanpa korupsi data.
- [ ] Claim code tidak bisa dipakai dua kali; QR lama mati setelah regenerate.
- [ ] CRUD user/meeting; hapus history benar-benar menghapus data.
- [ ] **WAN dicabut: meeting lokal tetap jalan penuh** (dashboard, login, join, chat, share screen).
- [ ] Nama lokal ter-resolve lewat dnsmasq di jaringan klien; cadangan IP/mDNS berfungsi.
- [ ] Meeting dari 4G / jaringan lain (Standard dan Sovereign).
- [ ] Campuran peserta lokal + internet dalam satu room.
- [ ] Lobby, kick, mute, mute all, lock room bekerja.
- [ ] Ganti `connectivity_mode` pada device tanpa flash ulang.

**Keamanan & privasi**
- [ ] Superadmin tidak punya jalur login ke dashboard/Pi.
- [ ] Heartbeat tidak memuat data user/room.
- [ ] Port scan eksternal: hanya yang direncanakan terbuka.
- [ ] Token LiveKit kedaluwarsa, tidak bisa dipakai ulang di room lain.
- [ ] Token Cloudflare di Pi hanya memiliki izin minimum.
- [ ] Sovereign: tidak ada trafik signaling melalui Cloudflare.
- [ ] E2EE on/off berfungsi.

**Sertifikat & jaringan**
- [ ] Sertifikat lokal valid tanpa peringatan; peringatan dashboard muncul bila perpanjangan gagal.
- [ ] Perubahan IP LAN: record DNS dan dnsmasq ikut terbarui.
- [ ] Router/AP dengan client isolation: dokumentasikan dan uji.

**Performa**
- [ ] 4/6/8/10 peserta: CPU, suhu, packet loss, kualitas.
- [ ] Suhu stabil < 75°C dengan casing + fan.
- [ ] Bandwidth TURN di Tencent terukur.

---

## 15. Risiko & Mitigasi

| Risiko | Dampak | Mitigasi |
|---|---|---|
| Tunnel HTTP tidak bawa UDP | Meeting internet gagal | TURN + port-forward opsional |
| Cloudflare men-terminate TLS (Standard) | Klaim privasi terlalu absolut | Mode Sovereign, dokumen privasi jujur, E2EE |
| Ketergantungan akun Cloudflare | Semua device Standard putus | Token sempit, 2FA, jalur cadangan untuk klien kritis |
| Token DNS tersimpan di Pi untuk DNS-01 | Bila Pi dibobol, token bocor | Token dibatasi per zona/record, atau sertifikat diterbitkan terpusat dan didistribusikan (trade-off privasi) |
| Nama lokal tidak ter-resolve di jaringan klien | Link lokal gagal | dnsmasq + panduan DHCP, cadangan IP/mDNS |
| Sertifikat kedaluwarsa saat offline lama | Peringatan browser | Auto-renew, peringatan dashboard, opsi CA privat |
| Fitur moderasi harus dibangun | Waktu dev bertambah | Bertahap: lobby/kick/mute dulu, raise hand/reaction belakangan |
| Kapasitas Pi 4B | Lag > ~10 peserta | Batasi per plan, simulcast/dynacast, LAN gigabit, fan |
| SD card rusak/clone | Downtime / kebocoran lisensi | SSD USB, backup config, token lisensi + heartbeat |
| Bandwidth Tencent (TURN) | Biaya naik | Monitoring, batas relay, utamakan direct/port-forward |
| Klaim "superadmin tidak bisa akses" tidak absolut | Kepercayaan | Support mode oleh Admin Client, OTA bertanda tangan + persetujuan, dokumentasi jujur |
| Client isolation / multicast diblokir | Koneksi LAN gagal | Matikan isolation, kabel untuk Pi |
| Perubahan versi LiveKit/Cloudflare API | Config break | Kunci versi, uji sebelum update |
| RAM Tencent 2 GB | Sesak bila banyak device | Tuning Postgres, swap, upgrade saat > ~30 device |

---

## 16. Estimasi Waktu (1 developer)

| Phase | Estimasi |
|---|---|
| 0 Persiapan | 1–2 hari |
| 1 Validasi LiveKit di Pi | 1–2 hari |
| 2 Akses lokal penuh | 3–4 hari |
| 3 Control plane | 3–5 hari |
| 4 Device base | 3–4 hari |
| 5 Tunnel & domain (2 mode) | 2–3 hari |
| 6 Dashboard Admin Client | 5–7 hari |
| 7 UI meeting + moderasi | 6–9 hari |
| 8 Lisensi | 2–3 hari |
| 9 Keamanan | 3–4 hari (paralel) |
| 10 Media internet | 2–3 hari |
| 11 Testing & packaging | 3–5 hari |
| **Total** | **± 6–8 minggu** |

---

## 17. Urutan Kerja yang Disarankan

1. **Hari 1–2:** LiveKit di Pi, uji 2–4 perangkat via LAN (Phase 1).
2. **Hari 3–6:** Caddy + dnsmasq + sertifikat lokal; uji tanpa internet (Phase 2).
3. **Hari 7–10:** App token issuer minimal + UI join sederhana.
4. **Minggu 2–3:** Control plane minimal + provisioning Cloudflare (Standard) + claim flow.
5. **Minggu 3–4:** Dashboard Admin Client, dua jenis link.
6. **Minggu 4–6:** UI custom + lobby + moderasi + TURN.
7. **Minggu 6–7:** Mode Sovereign (WireGuard) + lisensi.
8. **Minggu 7–8:** Hardening, load test, golden image, dokumentasi privasi.

---

## 18. Keputusan Terbuka (Putuskan Sebelum Phase 5)

- [ ] Rilis awal: Standard saja dulu, lalu Sovereign menyusul?
- [ ] Sovereign: WireGuard atau frp?
- [ ] TURN: bawaan LiveKit, coturn, atau layanan terkelola Cloudflare?
- [ ] Sertifikat lokal default: DNS-01 di Pi atau diterbitkan terpusat?
- [ ] DNS lokal: apakah Anda bisa memastikan klien mengarahkan DNS/DHCP ke Pi? Bila tidak, fallback apa yang diutamakan?
- [ ] Klaim pertama tanpa internet: didukung atau wajib online?
- [ ] Penyimpanan Pi: SD card atau SSD USB?
- [ ] E2EE: default on di Standard atau hanya Sovereign?
- [ ] Batas peserta per plan?
- [ ] Heartbeat boleh kirim jumlah meeting agregat atau nol data?
- [ ] Support mode diperlukan?
- [ ] Penagihan: manual atau payment gateway?
- [ ] Moderasi MVP: yang wajib vs menyusul?
- [ ] Bahasa UI: Indonesia, Inggris, atau keduanya?
