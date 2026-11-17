#!/usr/bin/env bash
# Point everything that embeds the LAN address at the current one: the local TLS certificate,
# LiveKit's node IP (what browsers send media to), and the Caddy/dnsmasq config. Called after
# the appliance joins a network (netsetup) and by the NetworkManager dispatcher on IP changes.
set -euo pipefail
ETC=/etc/appliance
TPL=/opt/appliance/templates
[ -s "$ETC/device_id" ] || exit 0   # not provisioned yet
LAN_IP=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") print $(i + 1)}')
[ -n "$LAN_IP" ] || exit 0
[ "$LAN_IP" = "$(cat "$ETC/lan_ip" 2>/dev/null)" ] && exit 0

exec 9>/run/vipcon-apply-lan-ip.lock
flock 9
DEVICE_ID=$(cat "$ETC/device_id"); ROOT_DOMAIN=$(cat "$ETC/root_domain")
echo "apply-lan-ip: $(cat "$ETC/lan_ip" 2>/dev/null || echo none) -> $LAN_IP"
"$TPL/gen-local-ca.sh" "$ETC/ca" "$(hostname)" "$LAN_IP" "$DEVICE_ID" "$ROOT_DOMAIN" >/dev/null
"$TPL/gen-ca-artifacts.sh" "$ETC/ca" >/dev/null
sed -i -E "s/^(\s*node_ip:).*/\1 $LAN_IP/" "$ETC/livekit.yaml"
"$TPL/render-config.sh" "$DEVICE_ID" "$ROOT_DOMAIN" "$(hostname)" "$LAN_IP"
echo "$LAN_IP" > "$ETC/lan_ip"
if systemctl is-active -q appliance.service; then
    (cd /opt/appliance && docker compose restart livekit caddy >/dev/null)
fi
