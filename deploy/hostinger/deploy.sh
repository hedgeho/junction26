#!/usr/bin/env bash
# Deploy to the Hostinger box.
# Usage: deploy/hostinger/deploy.sh [--local] [ssh-host]   (default host: hostinger, see README.md)
#   default  pull ghcr.io/hedgeho/junction26:<HEAD sha>, built by .github/workflows/ci.yml
#   --local  build the working tree on the box instead (fallback if CI is down)
set -euo pipefail
MODE=ghcr
if [ "${1:-}" = --local ]; then MODE=local; shift; fi
HOST=${1:-hostinger}
DIR=/opt/junction26
IMAGE=ghcr.io/hedgeho/junction26
cd "$(git rev-parse --show-toplevel)"

if [ "$MODE" = ghcr ]; then
    TAG=$(git rev-parse HEAD)
    if ! git branch -r --contains "$TAG" | \grep -q 'origin/main$'; then
        echo "HEAD $TAG is not on origin/main, so CI has not built it. Push first, or use --local." >&2
        exit 1
    fi
else
    TAG=local
fi

ssh "$HOST" "mkdir -p $DIR/src"
rsync -az --delete \
    --exclude .git --exclude .venv --exclude data --exclude .env \
    --exclude __pycache__ --exclude .pytest_cache --exclude .ruff_cache \
    ./ "$HOST:$DIR/src/"

ssh "$HOST" bash -s <<REMOTE
set -euo pipefail
bash $DIR/src/deploy/hostinger/bootstrap.sh
cd $DIR/src
echo TAG=$TAG > deploy/hostinger/.env
if [ $MODE = ghcr ]; then
    if ! docker pull -q $IMAGE:$TAG; then
        echo "pull failed: is the CI run for $TAG finished, and is the box logged in to ghcr.io (see README.md)?" >&2
        exit 1
    fi
else
    docker build -q -t $IMAGE:$TAG -f deploy/Dockerfile .
fi
docker compose -f deploy/hostinger/docker-compose.yml up -d --remove-orphans
docker image prune -f >/dev/null
docker compose -f deploy/hostinger/docker-compose.yml ps
REMOTE
