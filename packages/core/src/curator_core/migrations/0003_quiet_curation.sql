-- Additive contracts. Existing ingest tables and Bluesky users remain unchanged.
CREATE TABLE reader_accounts (
    id TEXT PRIMARY KEY, bluesky_did TEXT NOT NULL UNIQUE, created_at INTEGER NOT NULL
);
INSERT INTO reader_accounts SELECT lower(hex(randomblob(16))), did, created_at FROM users;
CREATE TABLE profile_revisions (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('public','private')),
    parent_id TEXT, content_hash TEXT NOT NULL, search_terms TEXT NOT NULL DEFAULT '[]',
    origin TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE TABLE profile_heads (
    user_id TEXT NOT NULL, kind TEXT NOT NULL, revision_id TEXT NOT NULL,
    PRIMARY KEY(user_id, kind)
);
CREATE TABLE reader_settings (
    user_id TEXT PRIMARY KEY, daily_limit INTEGER NOT NULL DEFAULT 10 CHECK(daily_limit BETWEEN 1 AND 50),
    timezone TEXT NOT NULL DEFAULT 'UTC', blocked_authors TEXT NOT NULL DEFAULT '[]',
    version INTEGER NOT NULL DEFAULT 1, refresh_requested INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE feedback_jobs (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL, feedback TEXT NOT NULL,
    idempotency_key TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
    lease_until INTEGER, error_code TEXT, created_at INTEGER NOT NULL, applied_revision TEXT, source_chunk_id TEXT,
    UNIQUE(user_id, idempotency_key)
);
CREATE INDEX feedback_pending ON feedback_jobs(state, created_at);
-- Pipeline-owned platform-neutral source facts. Curation only reads this table.
CREATE TABLE social_posts (
    platform TEXT NOT NULL CHECK(platform IN ('bluesky','x')), native_id TEXT NOT NULL,
    author_id TEXT NOT NULL, author_name TEXT NOT NULL, text TEXT NOT NULL,
    canonical_url TEXT NOT NULL, created_at TEXT, observed_at INTEGER NOT NULL,
    parent_id TEXT, root_id TEXT, quote_id TEXT, context TEXT NOT NULL DEFAULT '',
    deleted INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(platform, native_id)
);
CREATE INDEX social_posts_time ON social_posts(observed_at);
CREATE TABLE assessments_v2 (
    user_id TEXT NOT NULL, post_key TEXT NOT NULL, profile_revision TEXT NOT NULL,
    content_hash TEXT NOT NULL, scorer_version TEXT NOT NULL, decision TEXT NOT NULL,
    relevance REAL NOT NULL, substance REAL NOT NULL, opportunity REAL NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY(user_id, post_key, profile_revision, content_hash, scorer_version)
);
CREATE TABLE digest_runs (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, day TEXT NOT NULL, profile_revision TEXT NOT NULL,
    settings_version INTEGER NOT NULL, created_at INTEGER NOT NULL
);
CREATE INDEX digest_reader ON digest_runs(user_id, created_at);
CREATE TABLE digest_items (
    run_id TEXT NOT NULL, position INTEGER NOT NULL, post_key TEXT NOT NULL,
    content_hash TEXT NOT NULL, PRIMARY KEY(run_id, position)
);
CREATE TABLE dismissed_posts (
    user_id TEXT NOT NULL, post_key TEXT NOT NULL, created_at INTEGER NOT NULL,
    PRIMARY KEY(user_id, post_key)
);
CREATE TABLE note_versions (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL, text TEXT NOT NULL,
    content_hash TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE TABLE note_chunks (
    id TEXT PRIMARY KEY, note_id TEXT NOT NULL, start_offset INTEGER NOT NULL, end_offset INTEGER NOT NULL
);
CREATE TABLE note_matches (
    user_id TEXT NOT NULL, post_key TEXT NOT NULL, content_hash TEXT NOT NULL,
    private_revision TEXT NOT NULL, chunk_id TEXT NOT NULL, relevance REAL NOT NULL,
    PRIMARY KEY(user_id, post_key, content_hash, private_revision, chunk_id)
);

-- Shared source indexes used for bounded public candidate recall.
CREATE VIRTUAL TABLE social_posts_fts USING fts5(text,content='social_posts',content_rowid='rowid');
CREATE TRIGGER social_fts_ins AFTER INSERT ON social_posts BEGIN
    INSERT INTO social_posts_fts(rowid,text) VALUES (new.rowid,new.text);
END;
CREATE TRIGGER social_fts_del AFTER DELETE ON social_posts BEGIN
    INSERT INTO social_posts_fts(social_posts_fts,rowid,text) VALUES ('delete',old.rowid,old.text);
END;
CREATE TRIGGER social_fts_upd AFTER UPDATE OF text ON social_posts BEGIN
    INSERT INTO social_posts_fts(social_posts_fts,rowid,text) VALUES ('delete',old.rowid,old.text);
    INSERT INTO social_posts_fts(rowid,text) VALUES (new.rowid,new.text);
END;
CREATE TRIGGER posts_fts_upd AFTER UPDATE OF text ON posts BEGIN
    INSERT INTO posts_fts(posts_fts,rowid,text) VALUES ('delete',old.rowid,old.text);
    INSERT INTO posts_fts(rowid,text) VALUES (new.rowid,new.text);
END;
