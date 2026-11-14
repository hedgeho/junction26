# Quiet curation design

Implemented from `TECHNICAL_PLAN.md`, 2026-11-14. Replaces the original continuous Claude/Bluesky feed design.

## Visible behavior

The daily reader displays original posts and exact note chunks. Feedback produces fixed statuses without a conversational reply. Generated Markdown configuration is accessible through secondary settings. There are no generated summaries, reply drafts, explanations or bridge sentences, and no source-writing or publishing actions.

## State and compute

Hostinger owns SQLite WAL state and immutable Markdown revision files. Ingest writes source facts. Curation updates profiles through Gemma on Modal, filters public posts through Gemini, and creates daily snapshots. Remote inference never opens the application database.

Explicit public filtering preferences and private inferred interests have separate Markdown heads. Notes queue private-only updates using source chunk references; jobs do not duplicate source bodies. Gemini requests contain only allowlisted public fields. Private notes and inferred interests remain in the application/Modal boundary.

Database profile-head pointers switch with an expected-parent comparison. Retries recover interrupted profile commits. Note existence is rechecked before committing inferred interests. Back up profile files and DB state together. Legacy configuration is imported once, with enforceable account blocks preserved.

## Selection and source contracts

`SocialPost` normalizes Bluesky/X identity, URL, text, timestamps and conversation references. Existing Jetstream rows are adapted without replacing the ingest table. Normalized rows override legacy records, including tombstones. Jetstream deletes invalidate normalized copies.

Gemini returns include/exclude/insufficient-context decisions and bounded rubric scores. Coverage and IDs are validated. Cache keys include profile revision, source hash, model and scorer version. The coordinator reserves a daily per-user candidate budget before provider requests.

Selection applies a usefulness threshold, top-k ceiling, conversation/text deduplication and two-item author limit. Engagement is not the objective. Blocks, dismissals, source edits and deletes apply at read time. Daily editions remain stable until refresh, a profile/settings/note change or the next local day. Previously selected prior-day IDs are excluded. The optional Bluesky feed has no popular fallback.

Private matching shortlists up to 12 original chunks by lexical overlap, then Gemma chooses chunk IDs. The application retrieves exact Unicode source slices and verifies source hashes. Note removal deletes imported text, chunks, matches and pending source jobs. Private configuration history may retain inferred interests.

## Integration limits

- X source import, classification and rendering work. The pipeline owner supplies an authorized live collector. Sign-in currently uses Bluesky.
- Daily delivery only; source retention remains 48 hours.
- Notes are pasted snapshots; no external connector or automatic synchronization.
- Matching failures preserve the public digest; refresh retries matching. Cold Gemma startup needs live latency/GPU tuning.
- Tests prove software invariants, not model relevance, feedback nuance or production performance.
- Explicit public feedback is shared with Gemini. Sensitive source notes belong in notes import or the private-feedback path. Private interests never expand public discovery terms automatically.
- Topic quotas, configurable exploration, semantic duplicate detection and private embeddings are future improvements; current diversity uses authors, conversations and normalized exact text.
- Profile history, explicit feedback and digest metadata have no automatic account-erasure policy yet.

## Verification

Tests cover source-only responses, public allowlists, idempotent feedback, revision races, stale inference, note deletion during processing, both platforms, tombstones, CSRF and ownership. CI isolates package dependencies. Run synthetic provider checks and a labeled relevance evaluation before a public demo.
