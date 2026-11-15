-- Written by ingest. Likes and reposts by accounts that some reader follows, for social recall.
CREATE TABLE followed_engagement (
    actor_did   TEXT NOT NULL,
    subject_uri TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK(kind IN ('like','repost')),
    at          INTEGER NOT NULL,
    PRIMARY KEY(actor_did, subject_uri, kind)
);
CREATE INDEX followed_engagement_actor ON followed_engagement(actor_did, at);
CREATE INDEX followed_engagement_at ON followed_engagement(at);
