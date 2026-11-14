# Shared contracts

SQLite stays on one host. Ingest writes source facts; curation/web write configuration and judgments. Packages communicate through tables and `curator_core.sources`.

## Compatibility

`posts`, `posts_fts`, `follows`, `follow_sync` and ingest keys retain their original shape. `users.did` and `last_seen_at` remain compatible with follow sync. Engagement counters are approximate. Ingest retains sources for its configured window and honors deletes.

Legacy preferences/scores/feed tables remain for compatibility. The reader uses new contracts; existing prompt/preferences are bootstrapped on first reader/worker access.

## Added tables

- `reader_accounts`: opaque user IDs mapped to Bluesky sign-in identities.
- `profile_revisions`, `profile_heads`: immutable Markdown metadata/current pointers; bodies live under `data/profiles`, outside public serving.
- `reader_settings`, `feedback_jobs`: validated delivery settings, refresh versions, durable explicit/source feedback. Source jobs only update private configuration.
- `social_posts`: pipeline-owned Bluesky/X normalized source facts; curation reads only.
- `assessments_v2`: profile/content/model-aware public classifications.
- `digest_runs`, `digest_items`, `dismissed_posts`: snapshot selection and source references, never copied social bodies.
- `note_versions`, `note_chunks`, `note_matches`: immutable imported private snapshots, offsets and matches. External original files are never written.

Add numbered migrations; never edit a deployed migration. Coordinate shared contracts with the pipeline owner. Source lifecycle events are required, not just creation events.

## Public source import

Call `curator_ingest.imports.import_posts` or supply a UTF-8 JSON array to `curator-ingest import-posts <path>`:

```json
[
  {
    "platform": "x",
    "native_id": "1234567890",
    "author_id": "987654321",
    "author_name": "Original author",
    "text": "Original source text, unmodified.",
    "canonical_url": "https://x.com/i/status/1234567890",
    "created_at": "2026-11-14T10:00:00Z",
    "observed_at": 1794650400,
    "parent_id": null,
    "root_id": null,
    "quote_id": null,
    "context": "",
    "deleted": false
  }
]
```

Parent/root/quote references use platform-local IDs. Bluesky native IDs are AT URIs and author IDs are DIDs. X uses stable post/account IDs, not mutable handles. Blocking uses `bluesky:<author-id>` or `x:<author-id>`. Canonical links must use HTTPS and the platform's allowed domain. Context must be accessible original source text, never an AI summary.

Importing a record replaces that source version. Send `deleted=true` for a tombstone, retaining required identity/URL metadata for validation. Normalized rows override legacy Bluesky records even when deleted. Jetstream deletes invalidate normalized copies. Pruning retains tombstones while a matching legacy row exists, preventing fallback resurrection. This is a pipeline handoff, not a built-in X API collector.
