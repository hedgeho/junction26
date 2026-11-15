-- SQLite cannot alter a CHECK constraint, so rebuild social_posts. Rowids are
-- copied because social_posts_fts is an external-content index keyed by rowid.
CREATE TABLE social_posts_new (
    platform TEXT NOT NULL CHECK(platform IN ('bluesky','x','threads')), native_id TEXT NOT NULL,
    author_id TEXT NOT NULL, author_name TEXT NOT NULL, text TEXT NOT NULL,
    canonical_url TEXT NOT NULL, created_at TEXT, observed_at INTEGER NOT NULL,
    parent_id TEXT, root_id TEXT, quote_id TEXT, context TEXT NOT NULL DEFAULT '',
    deleted INTEGER NOT NULL DEFAULT 0, media TEXT NOT NULL DEFAULT '[]',
    author_handle TEXT, author_avatar TEXT, PRIMARY KEY(platform, native_id)
);
INSERT INTO social_posts_new(rowid, platform, native_id, author_id, author_name, text, canonical_url, created_at,
    observed_at, parent_id, root_id, quote_id, context, deleted, media, author_handle, author_avatar)
SELECT rowid, platform, native_id, author_id, author_name, text, canonical_url, created_at,
    observed_at, parent_id, root_id, quote_id, context, deleted, media, author_handle, author_avatar
FROM social_posts;
DROP TABLE social_posts;
ALTER TABLE social_posts_new RENAME TO social_posts;
CREATE INDEX social_posts_time ON social_posts(observed_at);
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
