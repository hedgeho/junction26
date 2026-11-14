-- Owned by curation. Reader-independent content flags (probabilities) for one post version.
CREATE TABLE post_flags (
    post_key       TEXT PRIMARY KEY,
    content_hash   TEXT NOT NULL,
    version        TEXT NOT NULL,
    clickbait      REAL NOT NULL,
    ragebait       REAL NOT NULL,
    misinformation REAL NOT NULL,
    spam_promo     REAL NOT NULL,
    low_effort     REAL NOT NULL,
    assessed_at    INTEGER NOT NULL
);
CREATE INDEX post_flags_assessed ON post_flags(assessed_at);
