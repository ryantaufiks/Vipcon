# Device onboarding: from new device to ready-to-claim

This guide covers everything from registering a new appliance in the console to handing it to a
client, ready to be claimed. Follow the steps in order.

| Who | Where | What |
|---|---|---|
| Superadmin | Console (`https://console.<root domain>`) | Registers the device, client and contract |
| Technician | Workstation + the Raspberry Pi | Builds, installs and verifies the appliance |
| Client admin | The meeting room | Connects the network, claims the device, runs meetings |

`<root domain>` is the control plane's `ROOT_DOMAIN` (for example `xrostr.com`). The public site
on the root domain only serves the landing page and the device API; the console lives on its own
host.

---

## 1. Register the device (superadmin)

1. **Plans**: make sure a plan exists. It sets the limits (participants, users, concurrent
   meetings) and the price in SGD.
2. **Devices → New**: enter a label (for example "Boardroom Pi, 3.5in LCD") and save.
   The next page appears **only once**:
   - **Download `device.json`** and keep it somewhere private. It holds the device secret and
     the claim code, and neither can be shown again. If it is lost before installation, register
     a new device.
   - Print the label if you use one.
3. **Clients → New**: create the client organisation.
4. **Contracts → New**: pick the client, this device and the plan. Set the start and end dates
   and the status to **active**.
   Without an active contract the device's licence is `no_contract` and it refuses to start new
   meetings. Claiming still works.
5. **Devices → the device**: assign the client and plan.
   - **Site ID** (optional): a short office-network name such as `acme`, which gives
     `https://acme.lan.<root domain>`. Without one, the device uses its automatic name
     `https://dev-<id>.lan.<root domain>`. Both get a trusted certificate automatically.
   - Names of the form `dev-…` and a few others (`www`, `admin`, …) are reserved.
6. **Branding** (optional): upload a logo and tagline, either global or per client. The device's
   screen picks it up within a minute or two.

## 2. Prepare the Raspberry Pi (once per Pi)

You need a Raspberry Pi 4 Model B, a Waveshare 3.5" (B) touch screen and a good 5 V / 3 A USB-C
power supply.

1. Flash **Raspberry Pi OS (64-bit)** with Raspberry Pi Imager. Enable SSH and create the
   admin user in the imager settings.
2. Install Docker and the Compose plugin:
   ```bash
   curl -fsSL https://get.docker.com | sudo sh
   ```
3. Enable the screen: follow Waveshare's instructions for the 3.5" (B) so that
   `/boot/firmware/config.txt` contains `dtoverlay=waveshare35b-v2`, then reboot.
   The screen should show the console.
4. Give the technician's workstation SSH access (add its public key to
   `~/.ssh/authorized_keys`).

## 3. Install and provision (technician)

On the workstation, from the repository root:

```bash
cd device/web && npm ci && npm run build && cd ../..
tar czf - --exclude=__pycache__ --exclude=node_modules --exclude='test_*.py' device \
  | ssh <user>@<pi> 'mkdir -p ~/vipcon-appliance && tar xzf - -C ~/vipcon-appliance'
scp device.json <user>@<pi>:~/vipcon-appliance/device.json
ssh <user>@<pi> 'chmod 600 ~/vipcon-appliance/device.json'
```

The installer refuses to run if `device/web/dist` has not been built.

On the Pi:

```bash
sudo ~/vipcon-appliance/device/provisioning/install-appliance.sh ~/vipcon-appliance/device.json
```

The first run takes several minutes, mostly building the container images. It:

- turns off services the appliance doesn't need;
- installs the stack (LiveKit, the device app, Caddy, dnsmasq, agent) to `/opt/appliance`;
- installs network setup (Wi-Fi hotspot when offline), the LCD kiosk and the boot splash;
- provisions the device from `device.json`. Secrets go to `/etc/appliance` as root-only files,
  and every copy of `device.json` outside `/etc/appliance` is shredded.

Re-running the installer **without** a `device.json` is a safe update: it refreshes code and
configuration and keeps the device's identity, users and meetings.

## 4. Verify before handing over

Check each of these before the device leaves your hands.

| Check | How | Expected |
|---|---|---|
| Heartbeat | Console → Devices → the device | Recent "last seen"; DNS row shows the name → the Pi's LAN IP |
| DNS | `dig +short <name>.lan.<root domain> @1.1.1.1` | The Pi's LAN IP |
| Certificate | Console → the device | "Let's Encrypt, valid until …" |
| Trusted HTTPS | `curl https://<name>.lan.<root domain>/api/v1/claim/status` (from the same LAN) | `{"claimed":false,…}` with no certificate error |
| Screen | Touch the screen | Activation QR; its link starts with `https://<name>.lan.<root domain>/claim` |

The certificate usually arrives within a few minutes of the first heartbeat. Until then, the
screen's links use the Pi's IP address, which works but shows a certificate warning.

## 5. At the client site (client admin)

1. **Network**
   - **Ethernet**: plug it in. The Pi gets an address by DHCP and connects automatically.
   - **Wi-Fi**: power on and wait. After about 90 seconds without a network, the screen shows a
     Wi-Fi QR for a temporary hotspot (`Vipcon-Setup-XXXX`, random password). Scan it with a
     phone; the setup page opens; choose the office Wi-Fi and enter its password.
   - A saved network or a plugged-in cable ends setup on its own.
2. **Claim**: on a phone on the **same network**, touch the screen and scan the activation QR.
   Create the Client Admin account. The claim code works once.
3. **Meet**: sign in at `https://<name>.lan.<root domain>/admin`, create meetings and share the
   links. People in the room touch the screen and scan the QR to see the meetings in progress
   and join from their browser.

### Moving a device

After a claim, the device can be switched off and moved. At the new site it reconnects as above.
It keeps its users, meetings and licence, and its `.lan` name follows the new IP address within a
minute or two of reaching the internet.

## Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| Phone says "site can't be reached" for the `.lan` link | Phone isn't on the same network, or is on mobile data | Join the office Wi-Fi |
| Same, on the right network | The router blocks DNS answers that point to private IPs (DNS rebind protection; common on hotel and some office routers) | Allow `lan.<root domain>` in the router, or use the Pi's IP address |
| Certificate warning | The certificate isn't installed yet, or the link uses the IP | Wait a few minutes after the first heartbeat; check the console |
| "Device not ready" on the screen | Not provisioned | Re-run the installer with `device.json` |
| Can't start meetings | No active contract (licence `no_contract` or `expired`) | Create or renew the contract in the console |
| Claim code lost or already used | — | Console → the device → **Regenerate QR**, then put the new code on the device (see below) |

## Resetting a device

- **New claim code** (console → the device → Regenerate QR): the device becomes unclaimed. Put
  the new code on the Pi in `/etc/appliance/claim_code` (root-only) and restart the kiosk:
  `sudo systemctl restart vipcon-kiosk`.
- **Wipe users and meetings, keep the identity**: in `/opt/appliance`, run
  `sudo docker compose stop app && sudo docker compose rm -f app && sudo docker volume rm appliance_appdata && sudo docker compose up -d app`.
- **Full factory reset** (removes identity, data, certificates and licence; the screen shows
  "Device not ready"): `sudo /opt/appliance/factory-reset.sh --yes`. Provision again with a
  `device.json` from a newly registered device.
