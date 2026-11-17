#!/usr/bin/env bash
# Install the Vipcon LCD kiosk and lock the Pi's local console down to it.
# Run as root on the appliance:  sudo ./install.sh
# The LCD driver itself is set up separately (see lcd-waveshare35b.sh).
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "== packages"
apt-get update -qq
apt-get install -y -qq python3-pil python3-numpy python3-qrcode python3-evdev fonts-dejavu-core >/dev/null

echo "== kiosk files"
install -d -m 755 /opt/appliance/kiosk /etc/appliance/kiosk
install -m 644 "$HERE/kiosk.py" /opt/appliance/kiosk/kiosk.py
install -m 644 "$HERE/vipcon-kiosk.service" /etc/systemd/system/vipcon-kiosk.service

echo "== console lockdown"
# No login prompt on the screen or any virtual terminal, no Ctrl+Alt+Del, no SysRq.
systemctl set-default multi-user.target >/dev/null
systemctl disable --now getty@tty1.service 2>/dev/null || true
systemctl mask getty@tty1.service autovt@.service ctrl-alt-del.target >/dev/null
install -d /etc/systemd/logind.conf.d
cat > /etc/systemd/logind.conf.d/vipcon-kiosk.conf <<'CONF'
[Login]
NAutoVTs=0
ReserveVT=0
CONF
echo "kernel.sysrq = 0" > /etc/sysctl.d/90-vipcon-kiosk.conf
sysctl -q -p /etc/sysctl.d/90-vipcon-kiosk.conf || true
# Disable any desktop autologin a stock image may have.
for dm in lightdm gdm3 sddm; do systemctl disable "$dm" 2>/dev/null || true; done

# Quiet boot: no text, cursor or logo on the panel before the kiosk takes over.
CMDLINE=/boot/firmware/cmdline.txt
[ -f "$CMDLINE" ] || CMDLINE=/boot/cmdline.txt
if [ -f "$CMDLINE" ]; then
    cp -n "$CMDLINE" "$CMDLINE.vipcon-orig"
    line=$(head -n1 "$CMDLINE")
    for opt in quiet loglevel=3 logo.nologo vt.global_cursor_default=0 consoleblank=0; do
        case " $line " in *" ${opt%%=*}="*|*" $opt "*) ;; *) line="$line $opt" ;; esac
    done
    printf '%s\n' "$line" > "$CMDLINE"
fi

systemctl daemon-reload
systemctl enable vipcon-kiosk.service >/dev/null
if systemctl is-active -q lightdm gdm3 sddm 2>/dev/null || pgrep -x Xorg >/dev/null; then
    # Stopping the desktop here could kill the terminal running this script.
    echo "== installed. The desktop is still running on the screen: reboot to finish (sudo reboot)."
else
    systemctl restart vipcon-kiosk.service
    echo "== done; status:"
    systemctl --no-pager --lines=5 status vipcon-kiosk.service || true
fi
