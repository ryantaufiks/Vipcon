#!/usr/bin/env bash
# Turn off everything a stock Raspberry Pi OS image runs that a Vipcon appliance does not need.
# Kept: docker/containerd, tailscaled, ssh, NetworkManager/wpa_supplicant (Wi-Fi), timesyncd,
# cron, journald, vipcon-kiosk.  Safe to run more than once.  Run as root.
set -uo pipefail
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }

off() {  # stop + disable + mask, skipping units that do not exist on this image
    for unit in "$@"; do
        if systemctl list-unit-files --no-legend "$unit" 2>/dev/null | grep -q .; then
            systemctl disable --now "$unit" >/dev/null 2>&1 || true
            systemctl mask "$unit" >/dev/null 2>&1 || true
            echo "  off: $unit"
        fi
    done
}

echo "== network-facing services nobody uses"
off rpcbind.service rpcbind.socket nfs-blkmap.service \
    cups.service cups.socket cups.path cups-browsed.service \
    avahi-daemon.service avahi-daemon.socket

echo "== desktop leftovers"
off bluetooth.service packagekit.service accounts-daemon.service udisks2.service \
    wayvnc-control.service wayvnc.service lcd35b-xconfig.service glamor-test.service rp1-test.service

# PackageKit's apt hook calls the (now masked) service after every dpkg run and makes apt fail.
HOOK=/etc/apt/apt.conf.d/20packagekit
if [ -e "$HOOK" ] && ! dpkg-divert --list "$HOOK" | grep -q .; then
    dpkg-divert --quiet --local --rename --divert "$HOOK.disabled" "$HOOK" && echo "  off: packagekit apt hook"
fi

echo "== cloud-init (first-boot settings from Raspberry Pi Imager are already applied)"
mkdir -p /etc/cloud && touch /etc/cloud/cloud-init.disabled && echo "  off: cloud-init"

echo "== AnyDesk (remote desktop listening on every interface)"
if dpkg -s anydesk >/dev/null 2>&1; then
    systemctl disable --now anydesk.service >/dev/null 2>&1 || true
    DEBIAN_FRONTEND=noninteractive apt-get purge -y -qq anydesk >/dev/null && echo "  removed: anydesk"
fi
rm -f /etc/apt/sources.list.d/anydesk*.list /etc/apt/sources.list.d/anydesk*.sources

systemctl daemon-reload
echo "== still running:"
systemctl list-units --type=service --state=running --no-legend --no-pager | awk '{print "  " $1}'
