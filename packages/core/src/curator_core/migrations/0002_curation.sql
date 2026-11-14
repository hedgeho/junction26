-- Owned by curation/web. Ingest reads users.did and users.last_seen_at only.

CREATE TABLE users (
    did          TEXT PRIMARY KEY,
    handle       TEXT,
    prompt       TEXT NOT NULL DEFAULT '',  -- freeform "what I want to see"
    created_at   INTEGER NOT NULL,
    last_seen_at INTEGER                    -- last getFeedSkeleton / site visit
);

-- Structured taste profile derived from feedback (same shape as doorss).
CREATE TABLE preferences (
    id         INTEGER PRIMARY KEY,
    user_did   TEXT NOT NULL,
    category   TEXT NOT NULL,   -- rule | topic | author_boost | author_block | search_term
    summary    TEXT NOT NULL,
    content    TEXT NOT NULL,
    active     INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL
);

CREATE TABLE scores (
    user_did  TEXT NOT NULL,
    post_uri  TEXT NOT NULL,
    score     REAL NOT NULL,    -- 0..1 relevance from the LLM
    reason    TEXT,
    model     TEXT NOT NULL,
    scored_at INTEGER NOT NULL,
    PRIMARY KEY (user_did, post_uri)
);

-- Materialized ranked feed; getFeedSkeleton only ever reads this.
CREATE TABLE feed_items (
    user_did TEXT NOT NULL,
    position INTEGER NOT NULL,
    post_uri TEXT NOT NULL,
    reason   TEXT,
    PRIMARY KEY (user_did, position)
);

CREATE TABLE interactions (
    user_did   TEXT NOT NULL,
    post_uri   TEXT,
    event      TEXT NOT NULL,   -- e.g. app.bsky.feed.defs#requestLess, site:hide
    created_at INTEGER NOT NULL
);
