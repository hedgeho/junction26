CREATE TABLE social_contributions (
    user_did TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    record_uri TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('comment', 'quote')),
    created_at INTEGER NOT NULL,
    PRIMARY KEY (user_did, record_uri)
);
CREATE INDEX social_contributions_source ON social_contributions(user_did, source_uri);
