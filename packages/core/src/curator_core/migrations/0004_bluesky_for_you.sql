-- Personalized feed membership belongs to the reader; source facts remain normalized.
CREATE TABLE bluesky_feed_sync (
    user_did TEXT PRIMARY KEY,
    feed_uri TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('loading','ready','auth_required','failed')),
    last_attempt INTEGER NOT NULL,
    updated_at INTEGER,
    post_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE bluesky_feed_posts (
    user_did TEXT NOT NULL,
    post_key TEXT NOT NULL,
    position INTEGER NOT NULL,
    observed_at INTEGER NOT NULL,
    PRIMARY KEY(user_did, post_key)
);
CREATE INDEX bluesky_feed_posts_reader ON bluesky_feed_posts(user_did, position);
