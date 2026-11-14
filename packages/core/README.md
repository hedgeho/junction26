# Shared contracts

SQLite stays on one host. Ingest and the authenticated Bluesky feed adapter write source facts through the shared validator; curation writes judgments and configuration. Packages communicate through tables and `curator_core.sources`.

## Compatibility

`posts`, `posts_fts`, `follows`, `follow_sync` and ingest keys retain their original shape. `users.did` and `last_seen_at` remain compatible with follow sync. Engagement counters are approximate. Ingest retains sources for its configured window and honors deletes.

Legacy preferences/scores/feed tables remain for compatibility. The reader uses new contracts; existing prompt/preferences are bootstrapped on first reader/worker access.

## Added tables

- `reader_accounts`: opaque user IDs mapped to Bluesky sign-in identities.
- `profile_revisions`, `profile_heads`: immutable Markdown metadata/current pointers; bodies live under `data/profiles`, outside public serving.
- `reader_settings`, `feedback_jobs`: validated delivery settings, refresh versions, durable explicit/source feedback. Source jobs only update private configuration.
- `social_posts`: normalized public Bluesky/X source facts; curation reads only.
- `bluesky_feed_sync`, `bluesky_feed_posts`: per-reader For You connection state and ordered snapshot membership. These do not copy OAuth tokens or private notes.
- `social_posts.author_handle`, `social_posts.author_avatar`: optional author display metadata from source adapters.
- `posts.media`, `social_posts.media`: JSON media references (images, video thumbnails, link cards); see `curator_core.media`. Bytes stay on platform CDNs.
- `assessments_v2`: profile/content/model-aware public classifications.
- `digest_runs`, `digest_items`, `dismissed_posts`: snapshot selection and source references, never copied social bodies.
- `note_versions`, `note_chunks`, `note_matches`: immutable imported private snapshots, offsets and matches. External original files are never written.

Add numbered migrations; never edit a deployed migration. Coordinate shared contracts with the pipeline owner. Source lifecycle events are required, not just creation events.

## Public source import

Call `curator_core.imports.import_posts` (also exported from `curator_ingest.imports`) or supply a UTF-8 JSON array to `curator-ingest import-posts <path>`:

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
    "deleted": false,
    "author_handle": "original_author",
    "author_avatar": "https://pbs.twimg.com/profile_images/1/avatar.jpg",
    "media": [
      {"type": "image", "thumb": "https://pbs.twimg.com/media/a.jpg", "full": "https://pbs.twimg.com/media/a.jpg?name=large", "alt": "Alt text"},
      {"type": "link", "url": "https://example.com/article", "title": "Title", "description": "", "thumb": null}
    ]
  }
]
```

Parent/root/quote references use platform-local IDs. Bluesky native IDs are AT URIs and author IDs are DIDs. X uses stable post/account IDs, not mutable handles. Blocking uses `bluesky:<author-id>` or `x:<author-id>`. Canonical links must use HTTPS and the platform's allowed domain. Context must be accessible original source text, never an AI summary.

`media`, `author_handle` and `author_avatar` are optional. Image/thumbnail/avatar URLs must be HTTPS on the platform's media hosts (`curator_core.media.MEDIA_HOSTS`); others are dropped. At most 4 images/videos and one link card are kept. Media is part of the content hash, so changing it causes the post to be rescored.

Importing a record replaces that source version. Send `deleted=true` for a tombstone, retaining required identity/URL metadata for validation. Normalized rows override legacy Bluesky records even when deleted. Jetstream deletes invalidate normalized copies. Pruning retains tombstones while a matching legacy row exists, preventing fallback resurrection. This is a pipeline handoff, not a built-in X API collector.

Curation owns `notes_folders`, `notes_files` and `notes_drive_folders`: these track per-reader local/Drive connections, derived source snapshots and folder ownership. `feedback_jobs.post_key` optionally associates feedback with the reader's original source post. Web configures connections and queues feedback; curation syncs Markdown snapshots before private note processing. No component writes original source files or Drive documents.
