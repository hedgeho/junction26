# Agora

A finite daily selection of original social posts, matched to the reader's interests and their own original notes. Feedback silently updates Markdown configuration. The main interface displays no generated prose.

Built at Junction 2026 by [hedgeho](https://github.com/hedgeho), [MiroKeimioniemi](https://github.com/MiroKeimioniemi) and [MZuhairKhan](https://github.com/MZuhairKhan). Live at [feed.ilia.fi](https://feed.ilia.fi).

## Architecture

- `core`: shared SQLite schema, migrations and platform-neutral source read contract.
- `ingest`: Bluesky Jetstream, follows, retention and normalized Bluesky/X import and an optional Threads keyword-search collector.
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

Sign in through Bluesky and complete setup; no initial interests are required. Adjust the post limit on the feed. Tell your feed updates future preferences, while Show more builds a new selection for the immediate request. Settings contain connections and the Feed curation / Note matching files with revision history and restore. Notes are read-only snapshots, refreshed periodically from connected sources.

```sh
uv run curator-curate once --user did:plc:example
uv run curator-curate check-providers
uv run curator-ingest import-posts candidates.json
uv run python -m pytest
uv run --exact --package curator-curation python -m pytest packages/curation
uv run python -m ruff check .
uv run python -m ruff format --check .
uv run --with playwright python scripts/check_reader_ui.py
```

`check-providers` performs synthetic live requests using configured credentials. The browser check requires local real feed data and Chromium (Microsoft Edge on Windows); it copies SQLite and profile files into temporary storage, blocks remote social writes, and simulates social/model failures. Reports and screenshots go to ignored `data/ui-audit/`. Package tests mock model responses. See `packages/core/README.md` for the import contract. Add numbered migrations; never edit deployed migrations. Coordinate schema and deploy changes with the pipeline owner.

## Deployment

Hostinger instructions remain in `deploy/hostinger/README.md`. SQLite and profile files stay on one host; never share its WAL file via Modal Volumes. Modal persists model weights only. Configure HTTPS, a strong session secret and a 512 KiB proxy body limit.

The optional Bluesky feed projects only Bluesky digest items, with no generated reasons or popular fallback. Publishing scripts register the feed record. The web client supports explicit user-initiated Bluesky comments, quotes, likes, reposts, saves and deletion of the user's own contributions. Original notes are never edited or published.

`TECHNICAL_PLAN.md` records scope. `DESIGN.md` describes implemented behavior and remaining limits. Live model quality, GPU fit and production deployment require separate smoke checks.

On first sign-in, complete **Setup** to confirm Bluesky and optionally connect up to ten Google Drive notes folders. A hosted app accepts link-shared folders through Context.dev, or private folders shared with its service account as Viewer. Local imports remain available to operators using `notes/` (ignored by Git). See [notes setup](packages/web/README.md#notes-connections). No sample-data preview is included.

## Built with

- [Gemini](https://ai.google.dev/) filters and scores public posts and screens out clickbait, ragebait, spam and misinformation.
- [Modal](https://modal.com/) serves the private Gemma model that edits profiles and matches notes to posts.
- [Hostinger](https://www.hostinger.com/) hosts the application, SQLite state and the live site.
- [CRACI](https://craci.com/) runs CI: lint, tests and image builds, with deny-by-default network egress.

## License

[MIT](LICENSE)
