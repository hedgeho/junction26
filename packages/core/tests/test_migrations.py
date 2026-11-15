import sqlite3
import threading
from pathlib import Path

import pytest
from curator_core import db


def test_migrations_apply_once(tmp_path):
    path = tmp_path / "x.db"
    db.connect(path)
    conn = db.connect(path)
    names = [r[0] for r in conn.execute("SELECT name FROM schema_migrations ORDER BY name")]
    assert names == [m[0] for m in db.migrations()]


def test_concurrent_startup(tmp_path):
    path = tmp_path / "x.db"
    errors = []

    def start():
        try:
            db.connect(path)
        except sqlite3.Error as e:
            errors.append(e)

    threads = [threading.Thread(target=start) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors


def test_statement_splitter_keeps_trigger_bodies_whole():
    sql = "CREATE TABLE a(x);\nCREATE TRIGGER t AFTER INSERT ON a BEGIN\n  SELECT 1;\n  SELECT 2;\nEND;\n"
    assert len(list(db._statements(sql))) == 2


def _migrate_before(path: Path, last: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE schema_migrations (name TEXT PRIMARY KEY, applied_at INTEGER NOT NULL)")
    for name, sql in db.migrations():
        if name >= last:
            break
        for stmt in db._statements(sql):
            conn.execute(stmt)
        conn.execute("INSERT INTO schema_migrations VALUES (?, 0)", (name,))
    return conn


def _post(platform: str, native_id: str, text: str) -> tuple[str, str, str, str, str, str, int]:
    return (platform, native_id, "author", "author", text, "https://x.com/i/status/1", 1)


_INSERT = (
    "INSERT INTO social_posts(platform,native_id,author_id,author_name,text,canonical_url,observed_at) "
    "VALUES (?,?,?,?,?,?,?)"
)


def test_threads_platform_migration_preserves_rows_and_search(tmp_path: Path) -> None:
    conn = _migrate_before(tmp_path / "x.db", "0016")
    conn.execute(_INSERT, _post("x", "1", "compilers are fun"))
    conn.execute("UPDATE social_posts SET media='[1]', author_handle='h', author_avatar='a'")
    db.migrate(conn)

    row = conn.execute("SELECT * FROM social_posts WHERE platform='x'").fetchone()
    assert (row["media"], row["author_handle"], row["author_avatar"]) == ("[1]", "h", "a")
    conn.execute(_INSERT, _post("threads", "9", "gardening tips"))
    fts = "SELECT p.native_id FROM social_posts_fts f JOIN social_posts p ON p.rowid=f.rowid WHERE social_posts_fts MATCH ?"
    assert [r[0] for r in conn.execute(fts, ("compilers",))] == ["1"]
    assert [r[0] for r in conn.execute(fts, ("gardening",))] == ["9"]
    conn.execute("UPDATE social_posts SET text='edited words' WHERE native_id='9'")
    assert not conn.execute(fts, ("gardening",)).fetchall()
    assert [r[0] for r in conn.execute(fts, ("edited",))] == ["9"]
    conn.execute("DELETE FROM social_posts WHERE native_id='9'")
    assert not conn.execute(fts, ("edited",)).fetchall()


def test_platform_check_still_rejects_unknown_platforms(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "x.db")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(_INSERT, _post("mastodon", "1", "text"))


def test_multiple_note_sources_migration_preserves_existing_snapshots(tmp_path):
    connection = _migrate_before(tmp_path / "notes.db", "0019")
    connection.execute("INSERT INTO notes_folders VALUES ('reader','drive:rootfolder123','ready')")
    connection.execute("INSERT INTO notes_files VALUES ('reader','entry.md','original-note-id')")
    connection.execute("INSERT INTO notes_drive_folders VALUES ('rootfolder123','reader')")
    db.migrate(connection)
    row = connection.execute("SELECT * FROM notes_files").fetchone()
    assert row["note_id"] == "original-note-id" and row["source_path"] == "drive:rootfolder123"
    assert (
        connection.execute("SELECT source_path FROM notes_source_drive_folders").fetchone()[0] == "drive:rootfolder123"
    )
    connection.execute("INSERT INTO notes_folders VALUES ('reader','drive:secondfolder123','ready')")
    connection.execute("INSERT INTO notes_files VALUES ('reader','entry.md','second-note-id','drive:secondfolder123')")
    assert connection.execute("SELECT count(*) FROM notes_files").fetchone()[0] == 2


@pytest.mark.parametrize("legacy", [False, True])
def test_feedback_context_repair_preserves_jobs_and_optional_context(tmp_path, legacy):
    path = tmp_path / "feedback-upgrade.db"
    connection = _migrate_before(path, "0021")
    if legacy:
        connection.execute("ALTER TABLE feedback_jobs DROP COLUMN feed_context")
    connection.execute(
        "INSERT INTO feedback_jobs (id,user_id,kind,feedback,idempotency_key,state,attempts,lease_until,error_code,created_at,applied_revision,source_chunk_id,post_key,post_context,mode) VALUES ('job','reader','public','More of this','submission','applied',2,9,'old-error',42,'revision',NULL,'bluesky:post','Original source context','preference')"
    )
    if not legacy:
        connection.execute("UPDATE feedback_jobs SET feed_context='Exact feed context'")
    before = dict(connection.execute("SELECT * FROM feedback_jobs").fetchone())
    db.migrate(connection)
    after = dict(connection.execute("SELECT * FROM feedback_jobs").fetchone())
    assert after == before | {"feed_context": None if legacy else "Exact feed context"}
    db.migrate(connection)
    assert dict(connection.execute("SELECT * FROM feedback_jobs").fetchone()) == after
    connection.execute(
        "INSERT INTO feedback_jobs(id,user_id,kind,feedback,idempotency_key,created_at,feed_context) VALUES ('new','reader','public','Thoughtful writing','new-key',43,'New source context')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO feedback_jobs(id,user_id,kind,feedback,idempotency_key,created_at) VALUES ('duplicate','reader','public','Duplicate','submission',44)"
        )
    assert connection.execute("SELECT 1 FROM sqlite_master WHERE name='feedback_pending'").fetchone()
    connection.close()
