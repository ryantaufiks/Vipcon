#!/usr/bin/env bash
set -euo pipefail

BOOT_JSON=/boot/firmware/device.json
ETC=/etc/appliance
TPL=/opt/appliance/templates

mkdir -p "$ETC"

if [ ! -f "$BOOT_JSON" ]; then
    echo "first-boot: no device.json found, nothing to provision" >&2
    exit 0
fi

python3 - "$BOOT_JSON" "$ETC" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
etc = sys.argv[2]
open(f"{etc}/device_id", "w").write(d["device_id"])
open(f"{etc}/device_secret", "w").write(d.get("device_secret", ""))
open(f"{etc}/claim_code", "w").write(d.get("claim_code", ""))
open(f"{etc}/connectivity_mode", "w").write(d.get("connectivity_mode", "standard"))
open(f"{etc}/root_domain", "w").write(d.get("root_domain", ""))
open(f"{etc}/control_plane_url", "w").write(d.get("control_plane_url", ""))
open(f"{etc}/license_pubkey.pem", "w").write(d.get("license_public_key", ""))
PY

# Environment overrides win, then device.json, then the defaults.
ROOT_DOMAIN="${ROOT_DOMAIN:-$(cat "$ETC/root_domain")}"
ROOT_DOMAIN="${ROOT_DOMAIN:-domainutama.com}"
CONTROL_PLANE_URL="${CONTROL_PLANE_URL:-$(cat "$ETC/control_plane_url")}"
CONTROL_PLANE_URL="${CONTROL_PLANE_URL:-https://control.$ROOT_DOMAIN}"

DEVICE_ID=$(cat "$ETC/device_id")
DEVICE_SECRET=$(cat "$ETC/device_secret")
MODE=$(cat "$ETC/connectivity_mode")
# The address the LAN reaches us on (not docker0 or tailscale0).
LAN_IP="${LAN_IP:-$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") print $(i + 1)}')}"
LAN_IP="${LAN_IP:-$(hostname -I | awk '{print $1}')}"
HOSTNAME=$(hostname)

LIVEKIT_KEY="lk_$(openssl rand -hex 8)"
LIVEKIT_SECRET=$(openssl rand -hex 32)
SESSION_SECRET=$(openssl rand -hex 32)
echo -n "$LIVEKIT_KEY" > "$ETC/livekit_key"
echo -n "$LIVEKIT_SECRET" > "$ETC/livekit_secret"
chmod 600 "$ETC/livekit_key" "$ETC/livekit_secret"

sed -e "s/__LIVEKIT_KEY__/$LIVEKIT_KEY/g" \
    -e "s/__LIVEKIT_SECRET__/$LIVEKIT_SECRET/g" \
    -e "s/__LAN_IP__/$LAN_IP/g" \
    "$TPL/livekit.yaml.tpl" > "$ETC/livekit.yaml"

"$TPL/gen-local-ca.sh" "$ETC/ca" "$HOSTNAME" "$LAN_IP" "$DEVICE_ID" "$ROOT_DOMAIN"
"$TPL/gen-ca-artifacts.sh" "$ETC/ca"

TPL="$TPL" ETC="$ETC" "$TPL/render-config.sh" "$DEVICE_ID" "$ROOT_DOMAIN" "$HOSTNAME" "$LAN_IP"

cat > "$ETC/app.env" <<EOF
LIVEKIT_URL=ws://127.0.0.1:7880
LIVEKIT_API_KEY=$LIVEKIT_KEY
LIVEKIT_API_SECRET=$LIVEKIT_SECRET
SESSION_SECRET=$SESSION_SECRET
ROOT_DOMAIN=$ROOT_DOMAIN
DEVICE_ID=$DEVICE_ID
CONTROL_PLANE_URL=$CONTROL_PLANE_URL
DATABASE_URL=sqlite:////data/app.db
EOF

cat > "$ETC/agent.env" <<EOF
CONTROL_PLANE_URL=$CONTROL_PLANE_URL
DEVICE_ID=$DEVICE_ID
DEVICE_SECRET=$DEVICE_SECRET
MODE=$MODE
EOF
chmod 600 "$ETC/app.env" "$ETC/agent.env"

cp "$BOOT_JSON" "$ETC/device.json"
chmod 600 "$ETC/device.json"
shred -u "$BOOT_JSON" 2>/dev/null || rm -f "$BOOT_JSON"

systemctl enable appliance.service
systemctl start appliance.service

echo "first-boot: provisioned device $DEVICE_ID ($MODE)"
