#!/bin/sh
# Start coturn: base.conf plus the values from .env. TURN over TLS (TURN_TLS_HOST, port 443) is
# switched on once the certs worker has issued its certificate; renewals are then loaded without a
# restart (SIGUSR2). Runs as root to bind 443; turnserver drops to nobody (proc-user).
set -eu
: "${TURN_SECRET:?set TURN_SECRET in .env}" "${TURN_PUBLIC_IP:?set TURN_PUBLIC_IP in .env}" "${TURN_PRIVATE_IP:?set TURN_PRIVATE_IP in .env}"
umask 077
RUN=/run/turn
SRC="/tls/${TURN_TLS_HOST:-none}"
mkdir -p "$RUN"
chmod 711 "$RUN"  # turnserver (nobody) re-reads the certificate files on reload; it can't list them
conf="$RUN/turnserver.conf"
{
    cat /etc/coturn/base.conf
    echo "static-auth-secret=$TURN_SECRET"
    echo "listening-ip=$TURN_PRIVATE_IP"
    echo "relay-ip=$TURN_PRIVATE_IP"
    echo "external-ip=$TURN_PUBLIC_IP/$TURN_PRIVATE_IP"
    echo "denied-peer-ip=$TURN_PUBLIC_IP"
} > "$conf"

copy_certs() {
    cp "$SRC/fullchain.pem" "$RUN/cert.pem" && cp "$SRC/privkey.pem" "$RUN/key.pem" &&
        chown nobody:nogroup "$RUN/cert.pem" "$RUN/key.pem"
}

tls=0
if [ -n "${TURN_TLS_HOST:-}" ] && [ -s "$SRC/fullchain.pem" ] && copy_certs; then
    tls=1
    printf 'tls-listening-port=%s\ncert=%s\npkey=%s\n' "${TURN_TLS_PORT:-443}" "$RUN/cert.pem" "$RUN/key.pem" >> "$conf"
    echo "turn: TLS on port ${TURN_TLS_PORT:-443} for $TURN_TLS_HOST"
else
    echo "no-tls" >> "$conf"
    [ -n "${TURN_TLS_HOST:-}" ] && echo "turn: no certificate for $TURN_TLS_HOST yet; TLS starts once it is issued"
fi

if [ -n "${TURN_TLS_HOST:-}" ]; then
    (
        last=$(cat "$SRC/fullchain.pem" 2>/dev/null | md5sum)
        while sleep 600; do
            now=$(cat "$SRC/fullchain.pem" 2>/dev/null | md5sum)
            [ "$now" = "$last" ] && continue
            last=$now
            if [ "$tls" = 1 ]; then
                copy_certs && kill -USR2 1 && echo "turn: certificate renewed, reloaded"
            else
                echo "turn: certificate issued, restarting with TLS"
                kill -TERM 1  # the container restarts (restart: unless-stopped)
            fi
        done
    ) &
fi
exec turnserver -c "$conf"
