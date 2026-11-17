#!/usr/bin/env bash
set -euo pipefail

OUT="${1:-/etc/appliance/ca}"
HOSTNAME="${2:-$(hostname)}"
LAN_IP="${3:-$(hostname -I | awk '{print $1}')}"
DEVICE_ID="${4:-}"
ROOT_DOMAIN="${5:-domainutama.com}"

mkdir -p "$OUT"
CA_KEY="$OUT/ca-key.pem"
CA_CERT="$OUT/ca.pem"
SERVER_KEY="$OUT/server-key.pem"
SERVER_CSR="$OUT/server.csr"
SERVER_CERT="$OUT/server.pem"

if [ ! -f "$CA_KEY" ]; then
    openssl genrsa -out "$CA_KEY" 4096
    openssl req -x509 -new -key "$CA_KEY" -sha256 -days 3650 \
        -subj "/CN=VIP VC Local CA" -out "$CA_CERT"
fi

SANS="DNS:localhost,DNS:${HOSTNAME}.lan,DNS:${HOSTNAME},IP:127.0.0.1,IP:${LAN_IP}"
if [ -n "$DEVICE_ID" ] && [ -n "$ROOT_DOMAIN" ]; then
    SANS="${SANS},DNS:${DEVICE_ID}.local.${ROOT_DOMAIN}"
fi

openssl genrsa -out "$SERVER_KEY" 2048
openssl req -new -key "$SERVER_KEY" -subj "/CN=${HOSTNAME}.lan" -out "$SERVER_CSR"

cat > "$OUT/extfile.cnf" <<EOF
subjectAltName=${SANS}
EOF

openssl x509 -req -in "$SERVER_CSR" -CA "$CA_CERT" -CAkey "$CA_KEY" \
    -CAcreateserial -out "$SERVER_CERT" -days 3650 -sha256 \
    -extfile "$OUT/extfile.cnf"

chmod 600 "$CA_KEY" "$SERVER_KEY"
echo "CA cert:      $CA_CERT"
echo "Server cert:  $SERVER_CERT"
echo "Server key:   $SERVER_KEY"
echo "SANs: $SANS"
