# Hostinger hackathon box

Serves https://feed.ilia.fi from the organizer-provided Hostinger KVM (195.179.193.90).
The DNS record lives in `~/personal/vps_deployment/dns/ilia_fi.tf`.

## Deploy

```sh
deploy/hostinger/deploy.sh                       # uses the `hostinger` alias from ~/.ssh/config
deploy/hostinger/deploy.sh root@195.179.193.90   # anyone else whose key is on the box
```

It rsyncs the working tree (minus `.git`, `.venv`, `data`, `.env`) to `/opt/junction26/src`,
builds `junction26:latest` on the box, and restarts compose. HTTPS comes from Hostinger's
preinstalled Traefik (`/docker/traefik`); leave it and the Hermes agent next to it alone. No GitHub credentials live on
the box. The first run installs Docker, enables ufw (22, 80, 443) and creates the secrets file.

## Secrets

Secrets live only on the box, in `/opt/junction26/.env` (mode 600). That's outside the build
context, so they never reach the image or git. `SESSION_SECRET` is generated on first deploy;
set `ANTHROPIC_API_KEY` yourself:

```sh
ssh -t hostinger nano /opt/junction26/.env
```

`HOSTNAME` is set in `docker-compose.yml`, not in the secrets file.

## Box layout

```
/opt/junction26/
  .env    secrets (600)
  data/   SQLite DB, OAuth sessions, oauth_jwk.json
  src/    rsynced repo (build context)
```

## One-off after the first deploy

```sh
ssh hostinger
cd /opt/junction26/src
docker compose -f deploy/hostinger/docker-compose.yml exec web python scripts/gen_oauth_key.py data/oauth_jwk.json
docker compose -f deploy/hostinger/docker-compose.yml restart web
docker compose -f deploy/hostinger/docker-compose.yml exec \
  -e BSKY_PDS=https://pds.ilia.fi -e BSKY_HANDLE=ilia.fi -e BSKY_APP_PASSWORD=... \
  web python scripts/publish_feed.py --name "Curated for you"
```

## Logs

```sh
ssh hostinger 'cd /opt/junction26/src && docker compose -f deploy/hostinger/docker-compose.yml logs -f --tail=100'
```
