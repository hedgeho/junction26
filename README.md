# Quiet Curator

A finite daily selection of original social posts, matched to the reader's interests and their own original notes. Feedback silently updates Markdown configuration. The main interface displays no generated prose.

## Architecture

- `core`: shared SQLite schema, migrations and platform-neutral source read contract.
- `ingest`: Bluesky Jetstream, follows, retention and normalized Bluesky/X import.
- `curation`: versioned public/private Markdown profiles, feedback jobs, Gemini filtering, private Gemma matching and daily digests.
- `web`: Bluesky identity login, source-only reader, feedback status and secondary settings.
- `deploy/modal`: authenticated Gemma serving. Hostinger owns application state and calls Modal for inference.

Dependencies remain `web -> curation -> core` and `ingest -> core`. Curation never modifies or publishes source posts or original notes. X import/classification/rendering are supported; an X API collector and X login are not included.

Gemini receives public posts and explicit filtering preferences. Private notes and inferred interests stay in the application/Modal processing boundary. Cloud inference is not on-device processing.

## Develop

```sh
cp deploy/.env.example .env
uv sync --all-packages
uv run curator-ingest run
uv run curator-curate run
uv run curator-web
```

Run processes in separate terminals. Open `http://127.0.0.1:8000` for development OAuth. Configure providers using `.env.example` and `deploy/modal/README.md`; model-dependent work remains pending without credentials. Set `GEMINI_MODEL` to an available event-account model.

Sign in through Bluesky, enter interests, and wait for processing. Settings provide optional Markdown access, version restore, delivery limits/timezone/blocks and private note import/removal. Notes are immutable snapshots; import a new snapshot and remove the old one when updating a note.

```sh
uv run curator-curate once --user did:plc:example
uv run curator-curate check-providers
uv run curator-ingest import-posts candidates.json
uv run python -m pytest
uv run --exact --package curator-curation python -m pytest packages/curation
uv run python -m ruff check .
uv run python -m ruff format --check .
```

`check-providers` performs synthetic live requests using configured credentials. Other tests mock model responses. See `packages/core/README.md` for the import contract. Add numbered migrations; never edit deployed migrations. Coordinate schema and deploy changes with the pipeline owner.

## Deployment

Hostinger instructions remain in `deploy/hostinger/README.md`. SQLite and profile files stay on one host; never share its WAL file via Modal Volumes. Modal persists model weights only. Configure HTTPS, a strong session secret and a 512 KiB proxy body limit.

The optional Bluesky feed projects only Bluesky digest items, with no generated reasons or popular fallback. Existing publishing scripts register the feed record; no social-post or notes publishing workflow exists.

`TECHNICAL_PLAN.md` records scope. `DESIGN.md` describes implemented behavior and remaining limits. Live model quality, GPU fit and production deployment require separate smoke checks.
