#!/usr/bin/env bash
# Ship the working tree to the Hostinger box, build there, restart.
# Usage: deploy/hostinger/deploy.sh [ssh-host]   (default: hostinger, see README.md)
set -euo pipefail
HOST=${1:-hostinger}
DIR=/opt/junction26
cd "$(git rev-parse --show-toplevel)"

ssh "$HOST" "mkdir -p $DIR/src"
rsync -az --delete \
    --exclude .git --exclude .venv --exclude data --exclude .env \
    --exclude __pycache__ --exclude .pytest_cache --exclude .ruff_cache \
    ./ "$HOST:$DIR/src/"

ssh "$HOST" bash -s <<REMOTE
set -euo pipefail
bash $DIR/src/deploy/hostinger/bootstrap.sh
cd $DIR/src
docker build -q -t junction26:latest -f deploy/Dockerfile .
docker compose -f deploy/hostinger/docker-compose.yml up -d --remove-orphans
docker image prune -f >/dev/null
docker compose -f deploy/hostinger/docker-compose.yml ps
REMOTE
