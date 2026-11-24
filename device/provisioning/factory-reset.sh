#!/usr/bin/env bash
# Return the appliance to an unprovisioned state: stop the stack, delete its data
# (users, meetings, settings), its identity, certificates and licence. The installed code,
# the LCD kiosk and the OS stay; the screen goes back to "Device not ready".
# Run as root:  sudo /opt/appliance/factory-reset.sh --yes
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
[ "${1:-}" = "--yes" ] || { echo "This erases all users, meetings and the device identity. Re-run with --yes." >&2; exit 1; }
ETC=/etc/appliance

systemctl disable --now appliance.service 2>/dev/null || true
if [ -f /opt/appliance/docker-compose.yml ]; then
    (cd /opt/appliance && docker compose down --volumes --remove-orphans) || true
fi
rm -rf "$ETC/ca" "$ETC/tls" "$ETC/kiosk/"*
rm -f "$ETC"/{device_id,device_secret,claim_code,connectivity_mode,root_domain,control_plane_url} \
      "$ETC"/{license_pubkey.pem,license.jwt,device.json,livekit_key,livekit_secret,livekit.yaml} \
      "$ETC"/{app.env,agent.env,Caddyfile,caddy-site.caddy,site_fqdn} \
      "$ETC"/{tunnel_token,public_fqdn,room_seed}
echo "factory reset done: the device is unprovisioned"
