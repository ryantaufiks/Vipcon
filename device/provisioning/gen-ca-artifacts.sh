#!/usr/bin/env bash
set -euo pipefail

CA_DIR="${1:-/etc/appliance/ca}"
CA_CERT="$CA_DIR/ca.pem"
[ -f "$CA_CERT" ] || { echo "gen-ca-artifacts: ca.pem not found in $CA_DIR" >&2; exit 1; }

cp "$CA_CERT" "$CA_DIR/ca.crt"

cat > "$CA_DIR/install-ca.sh" <<'EOF'
#!/bin/sh
set -e
CERT="$(dirname "$0")/ca.crt"
if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo $0"
  exit 1
fi
cp "$CERT" /usr/local/share/ca-certificates/vipcon-ca.crt
update-ca-certificates
if command -v certutil >/dev/null 2>&1; then
  mkdir -p "$HOME/.pki/nssdb"
  certutil -d "sql:$HOME/.pki/nssdb" -A -t "C,," -n "VIPCON CA" -i "$CERT" 2>/dev/null || true
fi
echo "VIPCON CA installed."
EOF
chmod +x "$CA_DIR/install-ca.sh"

cat > "$CA_DIR/install-ca.bat" <<'EOF'
@echo off
certutil -addstore -f "Root" "%~dp0ca.crt"
echo VIPCON CA installed.
pause
EOF

UUID1=$(cat /proc/sys/kernel/random/uuid)
UUID2=$(cat /proc/sys/kernel/random/uuid)
B64=$(base64 -w0 "$CA_CERT")

cat > "$CA_DIR/ca.mobileconfig" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>PayloadContent</key>
  <array>
    <dict>
      <key>PayloadCertificateFileName</key>
      <string>vipcon-ca.crt</string>
      <key>PayloadContent</key>
      <data>$B64</data>
      <key>PayloadDescription</key>
      <string>VIP Conference local root CA</string>
      <key>PayloadDisplayName</key>
      <string>VIPCON Root CA</string>
      <key>PayloadIdentifier</key>
      <string>com.vipcon.ca</string>
      <key>PayloadType</key>
      <string>com.apple.security.root</string>
      <key>PayloadUUID</key>
      <string>$UUID1</string>
      <key>PayloadVersion</key>
      <integer>1</integer>
    </dict>
  </array>
  <key>PayloadDisplayName</key>
  <string>VIPCON Root CA</string>
  <key>PayloadIdentifier</key>
  <string>com.vipcon.profile</string>
  <key>PayloadType</key>
  <string>Configuration</string>
  <key>PayloadUUID</key>
  <string>$UUID2</string>
  <key>PayloadVersion</key>
  <integer>1</integer>
</dict>
</plist>
EOF

echo "generated: ca.crt, install-ca.sh, install-ca.bat, ca.mobileconfig"
