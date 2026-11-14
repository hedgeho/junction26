#!/usr/bin/env bash
set -euo pipefail

exec 9>/opt/junction26/autodeploy.lock
flock -n 9 || exit 0
cd /opt/junction26/src
compose=(docker compose -f deploy/hostinger/docker-compose.yml)
image=ghcr.io/hedgeho/junction26
docker pull "$image:latest" >/dev/null
revision=$(docker image inspect "$image:latest" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
[[ "$revision" =~ ^[0-9a-f]{40}$ ]] || { echo "Invalid image revision" >&2; exit 1; }
old_revision=$(sed -n 's/^TAG=//p' deploy/hostinger/.env)
[[ "$old_revision" =~ ^[0-9a-f]{40}$ ]] || { echo "Invalid current revision" >&2; exit 1; }
[[ "$revision" != "$old_revision" ]] || exit 0
failed_file=/opt/junction26/autodeploy-failed
[[ ! -f "$failed_file" || "$(cat "$failed_file")" != "$revision" ]] || exit 0

docker pull "$image:$revision" >/dev/null
latest_id=$(docker image inspect "$image:latest" --format '{{.Id}}')
revision_id=$(docker image inspect "$image:$revision" --format '{{.Id}}')
[[ "$latest_id" == "$revision_id" ]] || { echo "Image tags differ; retrying later" >&2; exit 1; }
mkdir -p /opt/junction26/data/backups
chmod 700 /opt/junction26/data/backups
available=$(df -Pk /opt/junction26/data | awk 'NR==2 {print $4}')
database_size=$(du -k /opt/junction26/data/curator.db | awk '{print $1}')
(( available > database_size * 2 + 1048576 )) || { echo "Insufficient disk space for backup" >&2; exit 1; }
backup="/app/data/backups/pre-${revision:0:12}-$(date -u +%Y%m%d%H%M%S).sqlite3"
"${compose[@]}" exec -T web python -c 'import sqlite3,sys; src=sqlite3.connect("/app/data/curator.db"); dst=sqlite3.connect(sys.argv[1]); src.backup(dst); dst.close(); src.close()' "$backup" </dev/null
chmod 600 "/opt/junction26/data/backups/${backup##*/}"

rollback() {
    echo "Deployment failed; restoring previous image" >&2
    printf '%s\n' "$revision" > "$failed_file"
    printf 'TAG=%s\n' "$old_revision" > deploy/hostinger/.env
    "${compose[@]}" up -d --remove-orphans </dev/null || true
}
trap rollback EXIT
printf 'TAG=%s\n' "$revision" > deploy/hostinger/.env
"${compose[@]}" up -d --remove-orphans </dev/null
for attempt in {1..15}; do
    if curl -fsS --max-time 5 https://feed.ilia.fi/ >/dev/null \
        && "${compose[@]}" ps --status running --services | grep -Fxq web \
        && "${compose[@]}" ps --status running --services | grep -Fxq curate \
        && "${compose[@]}" ps --status running --services | grep -Fxq ingest; then
        trap - EXIT
        rm -f "$failed_file"
        echo "Deployed $revision"
        exit 0
    fi
    sleep 2
done
exit 1
