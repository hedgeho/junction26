-- Filesystem sources are read-only; these tables track derived snapshots only.
CREATE TABLE notes_folders (
    user_id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE, state TEXT NOT NULL DEFAULT 'ready'
);
CREATE TABLE notes_files (
    user_id TEXT NOT NULL, relative_path TEXT NOT NULL, note_id TEXT NOT NULL,
    PRIMARY KEY(user_id, relative_path)
);

ALTER TABLE feedback_jobs ADD COLUMN post_key TEXT;

CREATE TABLE notes_drive_folders (
    folder_id TEXT PRIMARY KEY, user_id TEXT NOT NULL
);
