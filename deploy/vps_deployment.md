# Suggested changes to ~/personal/vps_deployment (not applied)

This assumes the image is published as `ghcr.io/hedgeho/junction26` (the workflow in
`.github/workflows/ci.yml` pushes `ghcr.io/<owner>/<repo>` on push to `main`) and served at **feed.ilia.fi**.

## 1. `services/curator/docker-compose.yml` (new)

```yaml
x-curator: &curator
  image: ghcr.io/hedgeho/junction26:latest
  restart: unless-stopped
  volumes:
    - ./data:/app/data
  environment:
    - HOSTNAME=feed.ilia.fi
    - PUBLISHER_DID=did:plc:g5dlzj7xez24l7mt4narpmks
    - KEEP_LANGS=["en"]
    - GEMINI_API_KEY=${GEMINI_API_KEY}
    - GEMINI_MODEL=${GEMINI_MODEL}
    - GEMMA_URL=${GEMMA_URL}
    - GEMMA_TOKEN=${GEMMA_TOKEN}
    - SESSION_SECRET=${CURATOR_SESSION_SECRET}

services:
  curator-ingest:
    <<: *curator
    container_name: curator-ingest
    command: ["curator-ingest", "run"]
    cpus: "0.5"

  curator-worker:
    <<: *curator
    container_name: curator-worker
    command: ["curator-curate", "run"]
    cpus: "0.5"

  curator-web:
    <<: *curator
    container_name: curator-web
    command: ["curator-web"]
    cpus: "0.5"
    networks:
      - web

networks:
  web:
    external: true
```

Only `curator-web` joins the `web` network. Ingest and the worker only need outbound access.

## 2. `docker-compose.yml`: add to `include:`

```yaml
  - services/curator/docker-compose.yml  # LLM-curated Bluesky feeds (feed.ilia.fi)
```

## 3. `services/networking/Caddyfile`

```
feed.ilia.fi {
    log main

    reverse_proxy curator-web:8000
}
```

## 4. `dns/ilia_fi.tf`: add to `ilia_fi_records`

```hcl
    "feed/A"       = { ttl = 300, records = [local.vps_ipv4] }
```

If you also want IPv6 for this host, add `"feed/AAAA"`. Then run `tofu apply`; the pre-push
hook checks this.

## 5. `.env.example`

```sh
# services/curator: configure Gemini and private Gemma using deploy/.env.example
CURATOR_SESSION_SECRET=   # openssl rand -hex 32
```

## 6. One-off on the VPS after the first deploy

```sh
docker exec curator-web python scripts/gen_oauth_key.py data/oauth_jwk.json
docker restart curator-web
# Publish the feed record from your own PDS account (app password):
docker exec -e BSKY_PDS=https://pds.ilia.fi -e BSKY_HANDLE=ilia.fi -e BSKY_APP_PASSWORD=... \
  curator-web python scripts/publish_feed.py --name "Curated for you"
```

## Things to check on the VPS

- **Disk.** Expect about 3 GB of SQLite at 48h retention for English only. Lower
  `RETENTION_HOURS` if space is tight.
- **Memory.** Ingest uses well under 200 MB. SQLite page cache is the main consumer.
- **PDS backups** (from services/pds/README.md): publishing the feed writes a record to your
  repo. That's harmless, but another reason to automate the backup that README describes.
