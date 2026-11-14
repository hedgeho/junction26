-- Normalized text signature, written by ingest, for detecting one text posted from many accounts.
ALTER TABLE posts ADD COLUMN text_sig TEXT;
CREATE INDEX posts_text_sig ON posts(text_sig, indexed_at) WHERE text_sig IS NOT NULL;

-- Owned by curation. Platform moderation labels for candidate posts and their authors (author labels prefixed 'author:').
CREATE TABLE post_labels (
    post_key   TEXT PRIMARY KEY,
    labels     TEXT NOT NULL,  -- JSON array of label values
    checked_at INTEGER NOT NULL
);
CREATE INDEX post_labels_checked ON post_labels(checked_at);
