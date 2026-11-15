# Agora design

Implemented from `TECHNICAL_PLAN.md`, 2026-11-14. Replaces the original continuous Claude/Bluesky feed design.

## Visible behavior

The daily reader displays original posts and exact note chunks. Feedback updates Markdown configuration and reports progress plus a concise summary of actual profile changes. Configuration and revision history are accessible through settings. The reader displays original posts and supports user-initiated social actions; source notes are never modified.

## State and compute

Hostinger owns SQLite WAL state and immutable Markdown revision files. Ingest and the authenticated Bluesky feed adapter write source facts through a shared validator. Curation updates profiles through Gemma on Modal, filters public posts through Gemini, and creates daily snapshots. Remote inference never opens the application database.

Feed curation and note matching have separate Markdown heads. With `SCORING_BACKEND=gemma`, note chunks also queue feed-profile updates and Gemma scores candidates. The system marks a feed profile once notes influence it and blocks Gemini scoring for that reader to avoid sending note-derived interests to Gemini. With Gemini scoring, note-derived updates stay in the note-matching profile. Jobs reference note chunks rather than duplicating their text.

Database profile-head pointers switch with an expected-parent comparison. Retries recover interrupted profile commits. Note existence is rechecked before committing inferred interests. Back up profile files and DB state together. Legacy configuration is imported once, with enforceable account blocks preserved.

## Selection and source contracts

`SocialPost` normalizes Bluesky/X identity, URL, text, timestamps and conversation references. Existing Jetstream rows are adapted without replacing the ingest table. Normalized rows override legacy records, including tombstones. Jetstream deletes invalidate normalized copies.

Gemini returns include/exclude/insufficient-context decisions and bounded rubric scores. Coverage and IDs are validated. Cache keys include profile revision, source hash, model and scorer version. The coordinator reserves a daily per-user candidate budget before provider requests.

Selection applies a usefulness threshold, top-k ceiling, conversation/text deduplication and two-item author limit. Engagement is not the objective. Blocks, dismissals, source edits and deletes apply at read time. Daily editions remain stable until refresh, a profile/settings/note change or the next local day. Previously selected prior-day IDs are excluded. The optional Bluesky feed has no popular fallback.

Private matching shortlists up to 12 original chunks by lexical overlap, then Gemma chooses chunk IDs. The application retrieves exact Unicode source slices and verifies source hashes. Note removal deletes imported text, chunks, matches and pending source jobs. Private configuration history may retain inferred interests.

## Integration limits

- X source import, classification and rendering work. The pipeline owner supplies an authorized live collector. Sign-in uses Bluesky with read-only feed permissions.
- Daily delivery only; source retention remains 48 hours.
- Notes are read-only Markdown files from a host folder or a privately shared Google Drive folder. Drive supports public link-shared folders through Context.dev or private folders with operator-supplied service-account credentials; per-user Google OAuth is not implemented.
- Matching runs after a digest is published, so notes appear on a later read; failures preserve the public digest and refresh retries matching. The Modal definition keeps one Gemma GPU container warm; real latency and structured-output quality still need live validation.
- The worker wakes within a second of a refresh request, feedback submission or new reader instead of waiting for `CURATE_INTERVAL_S`, and curates up to `READER_CONCURRENCY` readers in parallel, each on its own connection. Per-stage timings for each reader's latest run are stored without source text; `curator-curate timings` prints them.
- Tests prove software invariants, not model relevance, feedback nuance or production performance.
- Feedback updates the feed curation profile through Gemma. Gemini scoring receives that profile only when it has not incorporated notes. Note-derived feed interests require Gemma scoring and may affect locally indexed candidate selection.
- Topic quotas, configurable exploration, semantic duplicate detection and private embeddings are future improvements; current diversity uses authors, conversations and normalized exact text.
- Profile history, explicit feedback and digest metadata have no automatic account-erasure policy yet.

## Verification

Tests cover source-only responses, public allowlists, idempotent feedback, revision races, stale inference, note deletion during processing, both platforms, tombstones, CSRF and ownership. CI isolates package dependencies. Run synthetic provider checks and a labeled relevance evaluation before a public demo.

## Reader interface

The product name in the UI is Agora. The visual language uses the brand's chalk, ink and olive palette, a meander mark and a restrained sun accent. Light and dark themes follow the system preference initially and allow a locally saved override. Shared local CSS/JavaScript provide responsive layouts, accessible dialogs, form loading states and conditional fields. The landing page loads its reference typefaces from Google Fonts, with local fallbacks; there are no browser-side model calls.

The reader is a single column of original post cards. The header contains Agora, Feed and Settings. Each post has its author, platform, original text, source link and one feedback icon. That icon opens a side panel with a feedback field. Feedback is queued with an owned post reference, then updates Markdown configuration and reports its changes. No slogans, generated replies or sample-content preview are shipped.

First sign-in opens setup: confirm the authenticated Bluesky source and optionally connect a Markdown folder. Both HTML and the digest API hide content until setup is complete. Candidate discovery uses the ingested public source pool; no sample posts are substituted.

Notes are original UTF-8 `.md` files in a local folder (default `notes/`) or a private Google Drive folder. Local paths are bounded by operator-configured `AGORA_NOTES_ROOTS`. The private Drive adapter uses a service account with the Drive read-only scope; users share a private folder with its email as Viewer and enter its folder URL. `GOOGLE_APPLICATION_CREDENTIALS` points to the host's secret JSON credential, never browser code or Git. For link-shared folders, Context.dev renders the folder listing and the app downloads original files directly from Google. Listings that cannot be verified fail; missing files are retained until disconnect to avoid deletion on partial listings. Anyone with the link can access the files. The app has no Drive write calls or source-note editing endpoint. Directories/Drive trees are owned per reader, preventing overlapping connections across accounts. Scans replace changed snapshots, remove missing files, queue private Gemma processing, and preserve exact original line endings. Failing scans retain the previous snapshot. Sync runs on reader/settings access and before curation; Drive requests are throttled to once per minute. Sources stay on their original storage; imported snapshots and configuration live on the app host. Disconnecting removes snapshots and matches but does not erase configuration history.

Configuration, delivery and connections are secondary settings. Model-maintained Markdown and revision history are collapsed by default. Setup/notes paths and provider constraints are described in `packages/web/README.md`.

## Candidate sources

Agora's candidate recall combines ingested posts from followed accounts, posts those accounts liked or reposted, the reader's own Bluesky For You feed, public topic matches, and posts gaining engagement fastest. Recent posts across supported sources only fill a pool smaller than `RECENT_BACKFILL_FLOOR`. For You pages are pulled in the background every 15 minutes with the reader's OAuth session and accumulate for a day; a failed or unauthenticated pull keeps earlier pages and never substitutes public content. Membership is per reader. Before any model call, candidates pass free screening: Bluesky moderation, self and author labels from the public AppView (cached; lookup failures fail open), copy-paste campaigns (one normalized text from `CAMPAIGN_MIN_AUTHORS` or more accounts in 24 hours), and, for authors the reader does not follow, `MAX_AUTHOR_POSTS_PER_DAY` and hashtag/mention stuffing. A digest delivers one post per linked story. Gemini Decisions then flags each candidate once, shared by all readers (`post_flags`): clickbait, ragebait and spam/promotion above calibrated thresholds hide a post; likely misinformation, borderline ragebait and low effort each halve its digest value but never hide it on their own. Flags are public-only inputs. Triage failures leave posts eligible and are retried. Relevance uses one Gemini Decisions request per reader and post (a 0-3 digest grade, `want`, substance and opportunity); a post is included when `want` >= 0.5 and grade/3 >= `DECISION_MINIMUM_RELEVANCE`. Digests reserve up to `SOCIAL_SLOTS` positions for followed authors that pass the filter and fill `SERENDIPITY_SHARE` with clean, substantive posts graded near but outside the profile. While today's digest is current, the worker scores up to `BACKGROUND_SCORE_SIZE` new candidates every `BACKGROUND_SCORE_INTERVAL_S`, so the pool stays scored, paced to leave a fifth of the daily cap for refreshes. Source posts can be displayed immediately while Gemini scores bounded batches concurrently; when scoring completes, a daily selection replaces the provisional candidates. A neutral baseline applies until feedback creates a public profile. Private notes are processed separately and are not sent to Gemini.

## Feedback and current notes

Post-specific feedback stores the selected post and its bounded context at submission so a later job can process it even after source expiry. General feedback includes the visible feed as context. Tell your feed saves preferences without rebuilding the current selection. Show more uses a temporary request as the primary selection criterion and makes at most a small addition to the durable profile. Both queue Gemma jobs and report progress in place. A final Gemma selection can assemble the eligible candidates, and a deterministic cap enforces the daily limit. Successful one-time requests are logically removed after the new selection is saved. Note folders are scanned periodically, and changed files enqueue jobs; model-derived interests in revision history may persist after notes are edited or removed. Curated posts are matched to original note excerpts.
