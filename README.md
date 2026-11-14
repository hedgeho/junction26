# junction26: personal, LLM-curated Bluesky feeds

One published feed generator serves every reader their own ranked feed. A small website is
where readers describe what they want and give feedback. Architecture, options and costs are
in [DESIGN.md](DESIGN.md).

## Layout and ownership

```
packages/
  core/       shared contract: settings, SQLite + migrations, row types   (both)
  ingest/     Jetstream → posts/FTS, follow sync, retention                (ingest + deploy owner)
  curation/   candidates → Claude scoring → ranking → feed_items           (curation + web owner)
  web/        feed generator XRPC endpoints + website + OAuth              (curation + web owner)
deploy/       Dockerfile, dev compose, .env.example, vps_deployment notes  (ingest + deploy owner)
scripts/      publish_feed.py, gen_oauth_key.py                            (ingest + deploy owner)
```

The packages talk only through SQLite tables. Ingest writes facts about Bluesky; curation
writes opinions about them. The contract between the two halves is in
[packages/core/README.md](packages/core/README.md).

Dependencies point one way: `web → curation → core` and `ingest → core`. CI runs each
package's tests with only its own dependencies installed, so an import across the line fails.

## Develop

```sh
cp deploy/.env.example .env           # set ANTHROPIC_API_KEY
uv sync --all-packages
uv run curator-ingest run             # terminal 1: Jetstream + follow sync
uv run curator-curate run             # terminal 2: scoring loop
uv run curator-web                    # terminal 3: http://127.0.0.1:8000 (use the IP, not localhost)
uv run pytest                         # everything
uv run --exact --package curator-ingest --with pytest pytest packages/ingest   # one package, as CI runs it
```

Other commands:

- `curator-ingest sync-follows <did>`: refresh one reader's follows now.
- `curator-ingest rates 60`: measure live Jetstream event rates.
- `curator-curate once --user <did>`: run one scoring cycle for one reader.

Add a dependency to a single package with `uv add --package curator-curation <dep>`.

## Schema changes

Add a new numbered file to `packages/core/src/curator_core/migrations/`. Never edit an
applied migration. Migrations run automatically when any process connects.

## Deploy

```sh
docker build -f deploy/Dockerfile .                                  # all packages, one image
docker build -f deploy/Dockerfile --build-arg PACKAGE=curator-ingest .   # slim, ingest only
docker compose -f deploy/docker-compose.yml up                       # local, all three processes
```

Production goes through `~/personal/vps_deployment`; see
[deploy/vps_deployment.md](deploy/vps_deployment.md). To publish the feed record:
`BSKY_PDS=https://pds.ilia.fi BSKY_HANDLE=... BSKY_APP_PASSWORD=... uv run python scripts/publish_feed.py`,
then set `PUBLISHER_DID`.
