#!/bin/sh
# Run by udev the moment the LCD's framebuffer appears during boot: blit the branded splash
# the kiosk cached on its last run. Plain shell + cat, so it shows ~2 s before Python could.
FB="/dev/${1:-fb0}"
RAW=/var/cache/vipcon-kiosk/splash.raw
SYS="/sys/class/graphics/${1:-fb0}"
[ -s "$RAW" ] && [ -w "$FB" ] || exit 0
# Only if the cached frame matches the panel's current geometry exactly.
height=$(cut -d, -f2 "$SYS/virtual_size" 2>/dev/null)
stride=$(cat "$SYS/stride" 2>/dev/null)
[ -n "$height" ] && [ -n "$stride" ] && [ "$(wc -c < "$RAW")" -eq $((height * stride)) ] || exit 0
cat "$RAW" > "$FB"
