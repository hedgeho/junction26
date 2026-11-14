import sqlite3
import threading

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
