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
    echo "created $DIR/.env: set ANTHROPIC_API_KEY there, then deploy again" >&2
fi
chmod 600 "$DIR/.env"

if ! grep -q '^ANTHROPIC_API_KEY=.' "$DIR/.env"; then
    echo "ANTHROPIC_API_KEY is empty in $DIR/.env; edit it with: ssh -t hostinger nano $DIR/.env" >&2
    exit 2
fi
