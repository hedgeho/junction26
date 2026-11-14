#!/usr/bin/env bash
# Runs on the box (deploy.sh calls it). Idempotent.
set -euo pipefail
DIR=/opt/junction26

if ! command -v docker >/dev/null; then
    curl -fsSL https://get.docker.com | sh
fi

if command -v ufw >/dev/null && ! ufw status | grep -q 'Status: active'; then
    ufw allow OpenSSH
    ufw allow 80/tcp
    ufw allow 443
    ufw --force enable
fi

mkdir -p "$DIR/data"

if [ ! -f "$DIR/.env" ]; then
    umask 077
    sed -e "s/^SESSION_SECRET=.*/SESSION_SECRET=$(openssl rand -hex 32)/" \
        -e "s/^PUBLISHER_DID=.*/PUBLISHER_DID=did:plc:g5dlzj7xez24l7mt4narpmks/" \
        -e '/^HOSTNAME=/d' \
        "$DIR/src/deploy/.env.example" > "$DIR/.env"
    echo "created $DIR/.env: set Gemini and Gemma configuration there, then deploy again" >&2
fi
chmod 600 "$DIR/.env"

for required in GEMINI_API_KEY GEMINI_MODEL GEMMA_URL GEMMA_TOKEN; do
    if ! grep -q "^${required}=." "$DIR/.env"; then
        echo "$required is empty in $DIR/.env; update the host configuration before deployment" >&2
        exit 2
    fi
done
