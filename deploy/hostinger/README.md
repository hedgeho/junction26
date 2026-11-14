# Hostinger hackathon box

Serves https://feed.ilia.fi from the organizer-provided Hostinger KVM (195.179.193.90).
The DNS record lives in `~/personal/vps_deployment/dns/ilia_fi.tf`.

## Deploy

Production runs the image CI built, so it matches the CRACI build record and SBOM for that
commit. On push to `main`, `.github/workflows/deploy_image.yml` builds on a CRACI runner and
pushes `ghcr.io/hedgeho/junction26:<sha>` and `:latest`. Once that run is green:

```sh
deploy/hostinger/deploy.sh                       # pulls the image for your HEAD commit
deploy/hostinger/deploy.sh root@195.179.193.90   # anyone else whose key is on the box
deploy/hostinger/deploy.sh --local               # fallback: build the working tree on the box
```

It rsyncs the working tree (minus `.git`, `.venv`, `data`, `.env`) to `/opt/junction26/src`
for the compose file and bootstrap script, pulls (or builds) the image, records the tag in
`src/deploy/hostinger/.env` and restarts compose. HTTPS comes from Hostinger's preinstalled
Traefik (`/docker/traefik`); leave it and the Hermes agent next to it alone. The first run
installs Docker, enables ufw (22, 80, 443) and creates the secrets file.

Production also runs `agora-autodeploy.timer`, which checks the registry once a minute.
The image workflow runs lint and tests before publishing `latest`; the timer pulls that
image and its matching commit tag, backs up SQLite, and starts the new image. It rolls
back the image on startup/health failure and will not retry the same failed revision.
Backups remain in `/opt/junction26/data/backups` and must be monitored and cleaned up
deliberately. Compose configuration and the timer script itself still require a manual
update on the host when they change. Inspect `systemctl status agora-autodeploy.timer`
and `journalctl -u agora-autodeploy.service` for deployment status.

### Registry login (once per box)

The package is private, so the box needs a read-only token. Create a classic GitHub PAT with
only the `read:packages` scope, then:

```sh
ssh -t hostinger 'docker login ghcr.io -u hedgeho'   # paste the PAT as the password
```

No GitHub write credentials live on the box.

## Secrets

Secrets live only on the box, in `/opt/junction26/.env` (mode 600). That's outside the build
context, so they never reach the image or git. `SESSION_SECRET` is generated on first deploy;
set `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMMA_URL` and `GEMMA_TOKEN` yourself (see `deploy/modal/README.md`):

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
