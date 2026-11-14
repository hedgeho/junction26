"""Reader rows and their preferences (curation-owned tables)."""

import sqlite3
import time


def ensure_user(conn: sqlite3.Connection, did: str) -> None:
    now = int(time.time())
    conn.execute(
        "INSERT INTO users(did, created_at, last_seen_at) VALUES(?, ?, ?) "
        "ON CONFLICT(did) DO UPDATE SET last_seen_at = excluded.last_seen_at",
        (did, now, now),
    )


def active_preferences(conn: sqlite3.Connection, did: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM preferences WHERE user_did = ? AND active = 1 ORDER BY id",
        (did,),
    ).fetchall()


def prefs_of(conn: sqlite3.Connection, did: str, category: str) -> list[str]:
    return [p["summary"] for p in active_preferences(conn, did) if p["category"] == category]
