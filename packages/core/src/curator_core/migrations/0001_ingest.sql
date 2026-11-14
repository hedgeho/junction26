-- Owned by ingest. Curation and web read these; only ingest writes them.

CREATE TABLE posts (
    uri          TEXT PRIMARY KEY,
    cid          TEXT NOT NULL,
    author_did   TEXT NOT NULL,
    text         TEXT NOT NULL,
    langs        TEXT,              -- comma-separated
    created_at   TEXT,              -- client-claimed, untrusted
    indexed_at   INTEGER NOT NULL,  -- unix seconds, from Jetstream time_us
    reply_parent TEXT,
    reply_root   TEXT,
    embed_kind   TEXT,              -- images | video | external | record | NULL
    embed_ref    TEXT,              -- link URL or quoted post URI
    likes        INTEGER NOT NULL DEFAULT 0,
    reposts      INTEGER NOT NULL DEFAULT 0,
    replies      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX posts_author ON posts(author_did, indexed_at);
CREATE INDEX posts_time ON posts(indexed_at);

-- Full-text index over posts.text, kept in sync by triggers.
CREATE VIRTUAL TABLE posts_fts USING fts5(
    text, content='posts', content_rowid='rowid', tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER posts_fts_ins AFTER INSERT ON posts BEGIN
    INSERT INTO posts_fts(rowid, text) VALUES (new.rowid, new.text);
END;
CREATE TRIGGER posts_fts_del AFTER DELETE ON posts BEGIN
    INSERT INTO posts_fts(posts_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
END;

-- Who each reader follows, refreshed from the AppView.
CREATE TABLE follows (
    user_did    TEXT NOT NULL,
    subject_did TEXT NOT NULL,
    PRIMARY KEY (user_did, subject_did)
);
CREATE INDEX follows_subject ON follows(subject_did);

CREATE TABLE follow_sync (
    user_did  TEXT PRIMARY KEY,
    synced_at INTEGER NOT NULL
);

CREATE TABLE kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
