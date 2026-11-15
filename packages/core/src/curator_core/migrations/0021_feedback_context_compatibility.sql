-- Some early deployments recorded 0018 before its optional feedback context
-- column was included. Rebuild from a staging snapshot so both schema variants
-- upgrade without changing old migrations or losing any existing context.
-- CREATE TABLE AS renames a duplicate output column; the original feed_context,
-- when present, keeps its name and therefore takes precedence over the fallback.
CREATE TEMP TABLE feedback_jobs_upgrade AS SELECT *, NULL AS feed_context FROM feedback_jobs;
DROP TABLE feedback_jobs;
CREATE TABLE feedback_jobs (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL, feedback TEXT NOT NULL,
    idempotency_key TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
    lease_until INTEGER, error_code TEXT, created_at INTEGER NOT NULL, applied_revision TEXT, source_chunk_id TEXT,
    post_key TEXT, post_context TEXT, mode TEXT NOT NULL DEFAULT 'preference', feed_context TEXT,
    UNIQUE(user_id, idempotency_key)
);
INSERT INTO feedback_jobs
SELECT id,user_id,kind,feedback,idempotency_key,state,attempts,lease_until,error_code,created_at,
       applied_revision,source_chunk_id,post_key,post_context,mode,feed_context
FROM feedback_jobs_upgrade;
DROP TABLE feedback_jobs_upgrade;
CREATE INDEX feedback_pending ON feedback_jobs(state, created_at);
