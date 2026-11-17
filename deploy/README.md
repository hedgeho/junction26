# Production: archived demo

https://feed.ilia.fi is archived. It runs the web process alone on the ilia.fi VPS (Hetzner),
deployed by [`hedgeho/vps_deployment`](https://github.com/hedgeho/vps_deployment) in
`services/junction26/`, with `ARCHIVED_ON=2026-11-15`. In that mode it serves the landing page,
`/demo`, the policy pages and the Bluesky feed generator; sign-in, reader pages, feedback and
background sync answer 410 or redirect to `/demo`. The Bluesky feed serves the demo reader's
picks to everyone. Nothing calls Gemini, Gemma or Context.dev, and no keys are configured. The
Modal Gemma app is stopped.

## The archive DB

`data/curator.db` on the VPS is built from the snapshot taken on 15 Nov 2026 09:36 UTC, before
ingest's 48-hour pruning emptied those digests:

```sh
HIDE_SHOWN_POSTS=false uv run --with freezegun python scripts/build_archive.py \
  nov15.sqlite3 curator.db --demo-did did:plc:mavrwfry6xk527brfgpj2qsf --at 2026-11-15T09:36:11+00:00
```

It keeps only the demo reader's account, settings, digests and the posts they show, plus the
funnel computed at that moment (the live funnel counts against the current day). Other readers,
notes, profiles, feedback and sessions are dropped. Run it outside the repo root so no `.env` is
read.

Copy the result to `vps_deployment/services/junction26/data/curator.db` on the VPS and restart
`junction26-web`.

## Releases

CI still publishes `ghcr.io/hedgeho/junction26:latest` once lint and tests pass, and the VPS cron
pulls it. Compose and Caddy changes go through `vps_deployment`.

## Running it live again

Unset `ARCHIVED_ON`, add `junction26-ingest` and `junction26-curate` services running
`curator-ingest run` and `curator-curate run` from the same image, provide the settings in
`deploy/.env.example` (Gemini credits, a redeployed Modal app per `deploy/modal/README.md`), and
start from a fresh DB or a full snapshot, not the archive.
