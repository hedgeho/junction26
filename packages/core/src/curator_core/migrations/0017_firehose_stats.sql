-- Owned by ingest. Hourly firehose post counts, before and after the language filter.
CREATE TABLE firehose_stats (
    hour         INTEGER PRIMARY KEY,  -- unix time / 3600
    posts        INTEGER NOT NULL,
    lang_dropped INTEGER NOT NULL
);
