"""SQLite connection and migrations shared by the ingest, curation and web processes.

WAL mode lets the three processes share one file: ingest is the only heavy
writer, curation writes scores/feeds, web mostly reads. If write contention
ever shows up, the next step is Postgres, not more SQLite tricks.

Migrations are the numbered files in migrations/, applied in order and recorded
in schema_migrations. Never edit an applied migration; add a new file. Table
ownership is in packages/core/README.md.
"""

import sqlite3
import time
from contextlib import contextmanager
from importlib import resources
from pathlib import Path

from .config import core_settings


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or core_settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    # Initial journal-mode negotiation can return SQLITE_BUSY without honoring
    # busy_timeout when several fresh processes open the database together.
    deadline = time.monotonic() + 30
    while True:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            break
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                conn.close()
                raise
            time.sleep(0.05)
    conn.execute("PRAGMA synchronous=NORMAL")
    migrate(conn)
    return conn


@contextmanager
def tx(conn: sqlite3.Connection):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _statements(sql: str):
    buf = ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                yield buf
            buf = ""
    if buf.strip():
        raise ValueError(f"incomplete SQL statement at end of migration: {buf[:80]!r}")


def migrations() -> list[tuple[str, str]]:
    files = resources.files(__package__).joinpath("migrations")
    return sorted((f.name, f.read_text()) for f in files.iterdir() if f.name.endswith(".sql"))


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at INTEGER NOT NULL)")
    pending = [m for m in migrations() if not _applied(conn, m[0])]
    if not pending:
        return
    # All processes may start at once; the write lock serialises them and the
    # re-check inside it makes the losers skip what the winner already applied.
    with tx(conn):
        for name, sql in pending:
            if _applied(conn, name):
                continue
            for stmt in _statements(sql):
                conn.execute(stmt)
            conn.execute("INSERT INTO schema_migrations VALUES (?, unixepoch())", (name,))


def _applied(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM schema_migrations WHERE name = ?", (name,)).fetchone() is not None


def kv_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def kv_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
