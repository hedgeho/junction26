# curator-core: the shared contract

Changes here need review from both owners (see CODEOWNERS).

## Tables

| Table | Writer | Readers | Guarantees |
|---|---|---|---|
| `posts` | ingest | curation, web | Last `RETENTION_HOURS` (48h) of posts in `KEEP_LANGS`. Deletes are honoured. `likes`/`reposts`/`replies` counters are approximate (they can over-count after a reconnect). |
| `posts_fts` | ingest (triggers) | curation | Always in sync with `posts.text` |
| `follows` | ingest | curation | Refreshed within `FOLLOWS_TTL_S` (6h) for every reader seen in the last 7 days |
| `follow_sync`, `kv` | ingest | — | Internal to ingest |
| `users` | curation/web | ingest (reads `did`, `last_seen_at` only) | A row exists for every reader who signed in or requested the feed |
| `preferences`, `scores`, `feed_items`, `interactions` | curation/web | — | Internal to the curation side |

Typed shapes of the ingest-owned rows are in `rows.py`. Adding a column is a minor change.
Removing or renaming one is a contract change: give it its own PR and get both owners to
review.

## What lives here

- `config.py`: `CoreSettings` (shared env vars) and `cached_settings()`. Each package
  subclasses `CoreSettings` with its own fields.
- `db.py`: `connect()` (WAL, runs migrations), `tx()`, `kv_get`/`kv_set`.
- `migrations/`: numbered SQL files. `0001` is ingest-owned and `0002` is curation-owned.
  New files go in the next number, and the owner of the tables they touch writes them.
- `testing.py`: the `conn` pytest fixture, plus `insert_post` and `add_user` for seeding the
  contract tables without importing another package.
