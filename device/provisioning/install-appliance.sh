#!/usr/bin/env bash
# Install the Vipcon appliance stack on a Raspberry Pi and, if a device.json is given,
# provision it as that device. Run as root from an unpacked bundle of the repo's device/ dir
# (with web/dist already built):
#
#     sudo device/provisioning/install-appliance.sh [path/to/device.json]
#
# Re-running is safe: code and templates are refreshed; an already-provisioned device keeps
# its identity unless a new device.json is passed.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
SRC="$(cd "$(dirname "$0")/.." && pwd)"   # the device/ directory
JSON="${1:-}"
BOOT_JSON=/boot/firmware/device.json
OPT=/opt/appliance
ETC=/etc/appliance

[ -f "$SRC/web/dist/index.html" ] || { echo "web/dist missing: run 'npm ci && npm run build' in device/web first" >&2; exit 1; }
command -v docker >/dev/null && docker compose version >/dev/null || { echo "docker + compose plugin required" >&2; exit 1; }

echo "== trim services"
"$SRC/kiosk/trim-services.sh" >/dev/null

echo "== code and templates -> $OPT"
install -d -m 755 "$OPT" "$OPT/templates" "$OPT/web" "$ETC"
install -m 644 "$SRC/docker-compose.yml" "$OPT/docker-compose.yml"
for dir in app agent; do
    rm -rf "${OPT:?}/$dir"
    cp -a "$SRC/$dir" "$OPT/$dir"
    find "$OPT/$dir" -name __pycache__ -prune -exec rm -rf {} +
done
install -m 644 "$SRC/livekit/livekit.yaml.tpl" "$SRC/caddy/Caddyfile.local.tpl" "$SRC/dnsmasq/dnsmasq.conf.tpl" "$OPT/templates/"
install -m 755 "$SRC/provisioning/gen-local-ca.sh" "$SRC/provisioning/gen-ca-artifacts.sh" \
    "$SRC/provisioning/render-config.sh" "$OPT/templates/"
install -m 755 "$SRC/provisioning/first-boot.sh" "$SRC/provisioning/factory-reset.sh" "$OPT/"
rm -rf "${OPT:?}/web/"*
cp -a "$SRC/web/dist/." "$OPT/web/"
install -m 644 "$SRC/systemd/appliance.service" /etc/systemd/system/appliance.service

echo "== network setup (Wi-Fi hotspot when offline, LAN IP follow-up)"
install -d -m 755 "$OPT/netsetup" /etc/NetworkManager/dnsmasq-shared.d /etc/NetworkManager/dispatcher.d
install -m 644 "$SRC/netsetup/netsetup.py" "$OPT/netsetup/netsetup.py"
install -m 644 "$SRC/netsetup/vipcon-netsetup.service" /etc/systemd/system/vipcon-netsetup.service
install -m 644 "$SRC/netsetup/vipcon-captive.conf" /etc/NetworkManager/dnsmasq-shared.d/vipcon-captive.conf
install -m 755 "$SRC/netsetup/90-vipcon-lan-ip" /etc/NetworkManager/dispatcher.d/90-vipcon-lan-ip
install -m 755 "$SRC/provisioning/apply-lan-ip.sh" "$OPT/apply-lan-ip.sh"
systemctl daemon-reload
systemctl enable vipcon-netsetup.service >/dev/null
systemctl restart vipcon-netsetup.service

if [ -n "$JSON" ]; then
    install -m 600 "$JSON" "$BOOT_JSON"
    shred -u "$JSON" 2>/dev/null || rm -f "$JSON"   # the secret now lives only in $BOOT_JSON
fi

echo "== container images (first run takes several minutes on a Pi)"
# Compose wants the env files to exist even just to build.
for f in app.env agent.env; do [ -f "$ETC/$f" ] || install -m 600 /dev/null "$ETC/$f"; done
[ -f "$ETC/livekit.yaml" ] || install -m 644 /dev/null "$ETC/livekit.yaml"
(cd "$OPT" && docker compose pull --quiet livekit caddy && docker compose build --quiet)

if [ -f "$BOOT_JSON" ]; then
    echo "== provisioning from $BOOT_JSON"
    "$OPT/first-boot.sh"
else
    if [ -s "$ETC/device_id" ]; then
        echo "== refreshing $(cat "$ETC/device_id")"
        LAN_IP="${LAN_IP:-$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") print $(i + 1)}')}"
        "$OPT/templates/render-config.sh" "$(cat "$ETC/device_id")" "$(cat "$ETC/root_domain")" "$(hostname)" "$LAN_IP"
        (cd "$OPT" && docker compose up -d --force-recreate)
    else
        echo "== device not provisioned yet; pass a device.json to provision it"
    fi
fi
(cd "$OPT" && docker compose ps --format '{{.Name}}\t{{.Status}}') || true

echo "== LCD kiosk"
"$SRC/kiosk/install.sh" | tail -n 3
