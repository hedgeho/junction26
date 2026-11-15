ALTER TABLE feedback_jobs ADD COLUMN mode TEXT NOT NULL DEFAULT 'preference';
CREATE TABLE feed_requests (
    user_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    instruction TEXT NOT NULL,
    feed_context TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
ALTER TABLE feedback_jobs ADD COLUMN feed_context TEXT;
