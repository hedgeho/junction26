CREATE TABLE note_match_batches (
    user_id TEXT NOT NULL, cache_key TEXT NOT NULL, state TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, cache_key)
);
