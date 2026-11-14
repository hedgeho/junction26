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
- Notes are pasted snapshots; no external connector or automatic synchronization.
- Matching failures preserve the public digest; refresh retries matching. Cold Gemma startup needs live latency/GPU tuning.
- Tests prove software invariants, not model relevance, feedback nuance or production performance.
- Explicit public feedback is shared with Gemini. Sensitive source notes belong in notes import or the private-feedback path. Private interests never expand public discovery terms automatically.
- Topic quotas, configurable exploration, semantic duplicate detection and private embeddings are future improvements; current diversity uses authors, conversations and normalized exact text.
- Profile history, explicit feedback and digest metadata have no automatic account-erasure policy yet.

## Verification

Tests cover source-only responses, public allowlists, idempotent feedback, revision races, stale inference, note deletion during processing, both platforms, tombstones, CSRF and ownership. CI isolates package dependencies. Run synthetic provider checks and a labeled relevance evaluation before a public demo.

## Reader interface

The product name in the UI is Agora. The visual language uses warm paper, dark olive text, a restrained terracotta accent, serif headlines and compact sans-serif source text. All assets are local CSS or inline icons; no third-party fonts, image services or browser-side model calls are needed.

The desktop reader has a narrow navigation rail, a finite selection and a secondary feedback panel. On smaller screens the navigation becomes horizontal and feedback follows the selection. Posts retain their original text and source link. Platform filters operate on the loaded daily selection without requesting more content. Matched notes are visually distinct exact source excerpts; no generated reasoning is displayed. The ending is explicit, with no infinite scroll or automatic refresh.

Feedback submission retains the existing CSRF-protected durable job flow and fixed status labels. Privacy selection stays beside the input. Delivery, snapshot imports, account blocks and optional Markdown configuration live in settings. Markdown configuration stays collapsed until opened. The interface does not introduce automatic posting or source editing.

A skip link, visible keyboard focus, labeled controls, pressed-state source filters, responsive layouts and reduced-motion support are included. Colors, spacing and typography are defined in the shared base template.

For local visual review without credentials, run `uv run python scripts/preview_ui.py` and open `http://127.0.0.1:8010/me`. This separate loopback-only server renders the production templates with explicitly labeled fictional sample posts and notes. It never accesses the application database, calls a model or grants an authenticated session. Preview form submissions are intercepted and show a fixed unsaved status; source filters and local dismissals work. Open `/me?empty=1` for the empty state, `/settings` for controls and `/` for sign-in. Real behavior remains available through `uv run python -m curator_web` on port 8000.

## Personalized Bluesky source

Agora reads the For You feed created by `spacecowboy17.bsky.social`, identified by the immutable URI `at://did:plc:3guzzweuqraryl3rdkimjamk/app.bsky.feed.generator/for-you`. The provider personalizes it using the signed-in reader, so public unauthenticated requests are not usable snapshots: a live check returned the provider's authentication notice as a pinned post. The adapter rejects that notice rather than passing it into curation.

Login requests granular feed-read RPC permissions: `getFeed` for the Bluesky AppView service, plus `getFeedSkeleton` for feed generators. The skeleton permission uses a wildcard audience to allow the PDS to authenticate its request to the selected generator; the application always requests the fixed For You URI. There are no record-write or interaction permissions. OAuth sessions stay in the existing server-side store. Requests use the SDK's refreshed DPoP authentication against the reader's PDS with the AppView proxy header. Old identity-only sessions need to reconnect.

A bounded page of up to 100 original posts is fetched on sign-in, on opening the reader when the snapshot is older than 15 minutes, and on explicit refresh. This is request-driven polling, not an unattended background collector. Source text is normalized without rewriting. Snapshot membership and ordering belong to each reader. Connected readers' Bluesky candidate pool comes exclusively from their own snapshot, interleaved with supplied X candidates; Gemini still makes the final public relevance decisions. The underlying For You ranking is candidate discovery, not Agora's objective.

Successful pulls invalidate the daily selection for rebuilding by the curation worker. Failed pulls preserve the previous snapshot and expose only fixed status text. Missing items in a refreshed page are removed from membership, not treated as source deletions. Existing retention, tombstones and read-time deletion checks continue to apply. Source normalization lives in the web adapter because it owns OAuth; normalized writes are shared in core, with no dependency from web to ingest. Existing unconnected readers retain the legacy source pipeline until connection.

References: `https://foryou.club/`, `https://atproto.com/specs/permissions`, `https://bsky.network/docs/bluesky-api/request-proxying/`. Software tests cover reader isolation, original text, authentication notices, failure preservation, request routing, scopes and CSRF. A signed-in live pull is still needed to verify the PDS/provider end to end.
