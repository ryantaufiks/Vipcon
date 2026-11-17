#!/usr/bin/env bash
# Render the Caddyfile and dnsmasq config for this device from the templates, and make sure
# dnsmasq is installed. Called by first-boot.sh and by install-appliance.sh on a refresh.
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
render "$TPL/dnsmasq.conf.tpl" > /etc/dnsmasq.conf

# dnsmasq-base (pulled in by NetworkManager) ships the binary but not the service.
if [ "$(dpkg-query -W -f='${Status}' dnsmasq 2>/dev/null)" != "install ok installed" ]; then
    DEBIAN_FRONTEND=noninteractive dpkg --configure -a || true   # finish an interrupted install
    apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq dnsmasq >/dev/null
fi
systemctl enable dnsmasq >/dev/null 2>&1 || true
systemctl restart dnsmasq || echo "render-config: dnsmasq did not start (see journalctl -u dnsmasq)" >&2
