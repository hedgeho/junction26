# Agora

**Junction table: A168.**

**The modern public square.** A social feed where you decide what's in it. Try it at [feed.ilia.fi](https://feed.ilia.fi), or browse the [demo feed](https://feed.ilia.fi/demo) without a Bluesky account.

Built at Junction 2026 by Blanka Bartfai, Ilia Zalesskii, Miro Keimiöniemi and Zuhair Khan.

## Why

Agora comes from a need we see across Gen Z, ourselves included. Gen Z spends more than six hours a day on their phones, mostly on social media. Eight in ten young people say they've seen misinformation online, and nearly half say social media is bad for their mental health. Most of us know this and keep scrolling anyway, because the feed is built to keep us there.

Social media makes money the longer you stay, so the feed shows you whatever keeps you there, and outrage and propaganda work well for that. By propaganda we don't only mean bots. We mean anything someone else wants you to see, chosen for you without asking.

Agora works the other way around. You decide what's in your feed.

## What it does

- **Tell your feed** in plain words what you want more or less of, for example "more European politics, less American" or "more startup posts", and it adjusts.
- **A feed you can finish.** Each feed has a limited number of posts (you can raise the limit), so you can actually reach the end.
- **Filtered before it reaches you.** Clickbait, rage bait, spam and coordinated campaigns are removed.
- **Your notes, connected.** If you keep notes, Agora can link them to related conversations, so the thought you wrote down at 2am has somewhere to go. It helps you share it in your own words, but it doesn't write for you.
- **Visible ranking.** The ranking formula is shown in the app, so you can see why each post is in your feed.

## How it works

Agora reads the public Bluesky stream, about 2.7 million posts a day. It narrows those down to posts that match what you asked for, then runs each one through a small, cheap model we set up to spot toxic content. We tuned it to be careful about what it removes, because we'd rather let a borderline post through than hide a real person. On our test sample it agrees with Claude Opus, a much more expensive model, about 90% of the time. The evaluations are in [`experiments/`](experiments/README.md).

Your feedback updates a plain Markdown configuration with full revision history, which you can read and restore in settings. The feed itself shows only original posts and your original note passages, never generated text. Private notes are processed by a self-hosted Gemma model on Modal and are never sent to Gemini.

We want social media to feel like a public square again: somewhere you go to talk, disagree and learn something, and then leave.

## Architecture

- `core`: shared SQLite schema, migrations and platform-neutral source read contract.
- `ingest`: Bluesky Jetstream, follows, retention and normalized Bluesky/X import and an optional Threads keyword-search collector.
- `curation`: versioned public/private Markdown profiles, feedback jobs, Gemini filtering, private Gemma matching and daily digests.
- `web`: Bluesky identity login, source-only reader, feedback status and secondary settings.
- `deploy/modal`: authenticated Gemma serving. The application host owns application state and calls Modal for inference.

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

Sign in through Bluesky and complete setup; no initial interests are required. Adjust the post limit on the feed. Tell your feed updates future preferences. Settings contain connections and the Feed curation / Note matching files with revision history and restore. Notes are read-only snapshots, refreshed periodically from connected sources.

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

feed.ilia.fi is an archived demo on the ilia.fi VPS; see `deploy/README.md`. SQLite and profile files stay on one host; never share its WAL file via Modal Volumes. Modal persists model weights only. Configure HTTPS, a strong session secret and a 512 KiB proxy body limit.

The optional Bluesky feed projects only Bluesky digest items, with no generated reasons or popular fallback. Publishing scripts register the feed record. The web client supports explicit user-initiated Bluesky comments, quotes, likes, reposts, saves and deletion of the user's own contributions. Original notes are never edited or published.

`TECHNICAL_PLAN.md` records scope. `DESIGN.md` describes implemented behavior and remaining limits. Live model quality, GPU fit and production deployment require separate smoke checks.

On first sign-in, complete **Setup** to confirm Bluesky and optionally connect up to ten Google Drive notes folders. A hosted app accepts link-shared folders through Context.dev, or private folders shared with its service account as Viewer. Local imports remain available to operators using `notes/` (ignored by Git). See [notes setup](packages/web/README.md#notes-connections).

Set `DEMO_DID` to a dedicated reader that has completed setup to show its feed read-only at `/demo`, without notes. Leave it empty to disable the demo.

## Built with

- [Gemini](https://ai.google.dev/) matches public posts to what readers asked for and filters out clickbait, rage bait, spam and coordinated campaigns.
- [Modal](https://modal.com/) serves the private Gemma model that edits profiles and matches notes to posts.
- [Hetzner](https://www.hetzner.com/) hosts the application, SQLite state and the live site.
- [CRACI](https://craci.com/) runs CI: lint, tests and image builds, with deny-by-default network egress.
- [Context.dev](https://context.dev/) reads link-shared Google Drive folder listings so readers can connect their notes.

## License

[MIT](LICENSE)
