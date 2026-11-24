#!/usr/bin/env bash
# Render the Caddyfile for this device from its template (and retire the old dnsmasq).
# Called by first-boot.sh, apply-lan-ip.sh and by install-appliance.sh on a refresh.
#   render-config.sh DEVICE_ID ROOT_DOMAIN HOSTNAME LAN_IP
set -euo pipefail
DEVICE_ID="$1"; ROOT_DOMAIN="$2"; HOST="$3"; LAN_IP="$4"
TPL="${TPL:-/opt/appliance/templates}"
ETC="${ETC:-/etc/appliance}"

# Templates use both __NAME__ and Caddy-style {$NAME} placeholders; fill in both, since the
# Caddy container is not given these variables.
render() {
    sed -e "s/__DEVICE_ID__/$DEVICE_ID/g"     -e "s/{\$DEVICE_ID}/$DEVICE_ID/g" \
        -e "s/__ROOT_DOMAIN__/$ROOT_DOMAIN/g" -e "s/{\$ROOT_DOMAIN}/$ROOT_DOMAIN/g" \
        -e "s/__HOSTNAME__/$HOST/g"           -e "s/{\$HOSTNAME}/$HOST/g" \
        -e "s/__LAN_IP__/$LAN_IP/g"           -e "s/{\$LAN_IP}/$LAN_IP/g" \
        "$1"
}
render "$TPL/Caddyfile.local.tpl" > "$ETC/Caddyfile"
# dnsmasq used to answer <host>.lan on the office network; the device's names now come from
# public DNS (<site>.lan.<root>, Let's Encrypt). Older installs: stop it (it answered DNS for the
# whole LAN on port 53).
if systemctl list-unit-files --no-legend dnsmasq.service 2>/dev/null | grep -q .; then
    systemctl disable --now dnsmasq >/dev/null 2>&1 || true
fi
