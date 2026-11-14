# curator-ingest

Run with `curator-ingest run`, which starts the Jetstream consumer and the follow-sync loop.

- `jetstream.py`: subscribes to posts, likes and reposts. Writes are batched every second,
  and it resumes from the saved cursor after a reconnect.
- `follows.py`: syncs follows from the AppView for active readers whose follow list is stale.
- `prune.py`: retention.

Measured 2026-11-14: about 40 posts/s and 250 likes/s overall, about 21 English posts/s.
Storage is about 780 B/post including FTS.

Next steps:
- Store likes by followed accounts, for the "posts liked by people you follow" candidate
  source.
- Jetstream v2 (`jetstream.us-east.bsky.network`).
- Periodic `VACUUM`.
