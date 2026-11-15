ALTER TABLE notes_folders RENAME TO notes_folders_single;
CREATE TABLE notes_folders (
    user_id TEXT NOT NULL, path TEXT NOT NULL UNIQUE, state TEXT NOT NULL DEFAULT 'ready',
    PRIMARY KEY (user_id, path)
);
INSERT INTO notes_folders SELECT * FROM notes_folders_single;
ALTER TABLE notes_files ADD COLUMN source_path TEXT NOT NULL DEFAULT '';
UPDATE notes_files SET source_path = COALESCE(
    (SELECT path FROM notes_folders_single WHERE notes_folders_single.user_id=notes_files.user_id), ''
);
ALTER TABLE notes_files RENAME TO notes_files_single;
CREATE TABLE notes_files (
    user_id TEXT NOT NULL, relative_path TEXT NOT NULL, note_id TEXT NOT NULL, source_path TEXT NOT NULL,
    PRIMARY KEY (user_id, source_path, relative_path)
);
INSERT INTO notes_files SELECT * FROM notes_files_single;
DROP TABLE notes_files_single;
CREATE TABLE notes_source_drive_folders (
    user_id TEXT NOT NULL, source_path TEXT NOT NULL, folder_id TEXT NOT NULL,
    PRIMARY KEY (user_id, source_path, folder_id)
);
INSERT INTO notes_source_drive_folders
SELECT d.user_id, n.path, d.folder_id FROM notes_drive_folders d
JOIN notes_folders_single n ON n.user_id=d.user_id;
DROP TABLE notes_folders_single;
