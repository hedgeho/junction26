# Agora design

Implemented from `TECHNICAL_PLAN.md`, 2026-11-14. Replaces the original continuous Claude/Bluesky feed design.

## Visible behavior

The daily reader displays original posts and exact note chunks. Feedback produces fixed statuses without a conversational reply. Generated Markdown configuration is accessible through secondary settings. There are no generated summaries, reply drafts, explanations or bridge sentences, and no source-writing or publishing actions.

## State and compute

Hostinger owns SQLite WAL state and immutable Markdown revision files. Ingest and the authenticated Bluesky feed adapter write source facts through a shared validator. Curation updates profiles through Gemma on Modal, filters public posts through Gemini, and creates daily snapshots. Remote inference never opens the application database.

Explicit public filtering preferences and private inferred interests have separate Markdown heads. Notes queue private-only updates using source chunk references; jobs do not duplicate source bodies. Gemini requests contain only allowlisted public fields. Private notes and inferred interests remain in the application/Modal boundary.

Database profile-head pointers switch with an expected-parent comparison. Retries recover interrupted profile commits. Note existence is rechecked before committing inferred interests. Back up profile files and DB state together. Legacy configuration is imported once, with enforceable account blocks preserved.

## Selection and source contracts

`SocialPost` normalizes Bluesky/X identity, URL, text, timestamps and conversation references. Existing Jetstream rows are adapted without replacing the ingest table. Normalized rows override legacy records, including tombstones. Jetstream deletes invalidate normalized copies.

Gemini returns include/exclude/insufficient-context decisions and bounded rubric scores. Coverage and IDs are validated. Cache keys include profile revision, source hash, model and scorer version. The coordinator reserves a daily per-user candidate budget before provider requests.

Selection applies a usefulness threshold, top-k ceiling, conversation/text deduplication and two-item author limit. Engagement is not the objective. Blocks, dismissals, source edits and deletes apply at read time. Daily editions remain stable until refresh, a profile/settings/note change or the next local day. Previously selected prior-day IDs are excluded. The optional Bluesky feed has no popular fallback.

Private matching shortlists up to 12 original chunks by lexical overlap, then Gemma chooses chunk IDs. The application retrieves exact Unicode source slices and verifies source hashes. Note removal deletes imported text, chunks, matches and pending source jobs. Private configuration history may retain inferred interests.

## Integration limits

- X source import, classification and rendering work. The pipeline owner supplies an authorized live collector. Sign-in uses Bluesky with read-only feed permissions.
- Daily delivery only; source retention remains 48 hours.
- Notes are read-only Markdown files from a host folder or a privately shared Google Drive folder. Drive needs operator-supplied service-account credentials; per-user Google OAuth is not implemented.
- Matching runs after a digest is published, so notes appear on a later read; failures preserve the public digest and refresh retries matching. Cold Gemma startup needs live latency/GPU tuning.
- The worker wakes within a second of a refresh request, feedback submission or new reader instead of waiting for `CURATE_INTERVAL_S`, and curates up to `READER_CONCURRENCY` readers in parallel, each on its own connection. Per-stage timings for each reader's latest run are stored without source text; `curator-curate timings` prints them.
- Tests prove software invariants, not model relevance, feedback nuance or production performance.
- Explicit public feedback is shared with Gemini. Sensitive source notes belong in notes import or the private-feedback path. Private interests never expand public discovery terms automatically.
- Topic quotas, configurable exploration, semantic duplicate detection and private embeddings are future improvements; current diversity uses authors, conversations and normalized exact text.
- Profile history, explicit feedback and digest metadata have no automatic account-erasure policy yet.

## Verification

Tests cover source-only responses, public allowlists, idempotent feedback, revision races, stale inference, note deletion during processing, both platforms, tombstones, CSRF and ownership. CI isolates package dependencies. Run synthetic provider checks and a labeled relevance evaluation before a public demo.

## Reader interface

The product name in the UI is Agora. The visual language uses a pale neutral surface, dark green text, compact sans-serif typography, restrained borders and rounded controls. Shared local CSS/JavaScript provide responsive layouts, accessible dialogs, form loading states and conditional fields. All assets are local files or inline icons; no third-party fonts, image services or browser-side model calls are needed.

The reader is a single column of original post cards. The header contains Agora, Feed and Settings. Each post has its author, platform, original text, source link and one feedback icon. That icon opens a native dialog with the original post and a feedback field. Feedback is queued with an owned post reference, then updates Markdown configuration; the UI receives fixed status labels only. No slogans, generated replies or sample-content preview are shipped.

First sign-in opens setup: confirm the authenticated Bluesky source and optionally connect a Markdown folder. Both HTML and the digest API hide content until setup is complete. Candidate discovery uses the ingested public source pool; no sample posts are substituted.

Notes are original UTF-8 `.md` files in a local folder (default `notes/`) or a private Google Drive folder. Local paths are bounded by operator-configured `AGORA_NOTES_ROOTS`. The Drive adapter uses a service account with the Drive read-only scope; users share a private folder with its email as Viewer and enter its folder URL. `GOOGLE_APPLICATION_CREDENTIALS` points to the host's secret JSON credential, never browser code or Git. The app has no Drive write calls or source-note editing endpoint. Directories/Drive trees are owned per reader, preventing overlapping connections across accounts. Scans replace changed snapshots, remove missing files, queue private Gemma processing, and preserve exact original line endings. Failing scans retain the previous snapshot. Sync runs on reader/settings access and before curation; Drive requests are throttled to once per minute. Sources stay on their original storage; imported snapshots and configuration live on the app host. Disconnecting removes snapshots and matches but does not erase configuration history.

Configuration, delivery and connections are secondary settings. Model-maintained Markdown and revision history are collapsed by default. Setup/notes paths and provider constraints are described in `packages/web/README.md`.

## Candidate sources

Agora's candidate recall combines ingested posts from followed accounts, public topic matches, and recent discovery across supported sources. Recent discovery is a shared public pool, not a personalized Bluesky feed. Before any model call, candidates pass free screening: Bluesky moderation, self and author labels from the public AppView (cached; lookup failures fail open), copy-paste campaigns (one normalized text from `CAMPAIGN_MIN_AUTHORS` or more accounts in 24 hours), and, for authors the reader does not follow, `MAX_AUTHOR_POSTS_PER_DAY` and hashtag/mention stuffing. A digest delivers one post per linked story. Source posts can be displayed immediately while Gemini scores bounded batches concurrently; when scoring completes, a daily selection replaces the provisional candidates. A neutral baseline applies until feedback creates a public profile. Private notes are processed separately and are not sent to Gemini.
