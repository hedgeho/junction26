"""Immutable Markdown revisions; the DB pointer is the only mutable authority."""

import hashlib
import json
import os
import time
import uuid
from pathlib import Path

from curator_core import db

from .config import settings

KINDS = {"public", "private"}


class RevisionConflict(Exception):
    pass


def reader_id(conn, did: str) -> str:
    row = conn.execute("SELECT id FROM reader_accounts WHERE bluesky_did=?", (did,)).fetchone()
    if row:
        return row["id"]
    uid = uuid.uuid4().hex
    conn.execute("INSERT OR IGNORE INTO reader_accounts VALUES (?,?,?)", (uid, did, int(time.time())))
    return conn.execute("SELECT id FROM reader_accounts WHERE bluesky_did=?", (did,)).fetchone()["id"]


def head(conn, user_id: str, kind: str = "public"):
    return conn.execute(
        """SELECT r.* FROM profile_heads h JOIN profile_revisions r ON r.id=h.revision_id
        WHERE h.user_id=? AND h.kind=?""",
        (user_id, kind),
    ).fetchone()


def _path(user_id: str, kind: str, revision: str) -> Path:
    if kind not in KINDS or any(
        len(v) != 32 or any(c not in "0123456789abcdef" for c in v) for v in (user_id, revision)
    ):
        raise ValueError("invalid revision path")
    return settings().data_dir / "profiles" / user_id / kind / f"{revision}.md"


def markdown(conn, user_id: str, kind: str = "public") -> str:
    current = head(conn, user_id, kind)
    if not current:
        return ""
    body = _path(user_id, kind, current["id"]).read_text(encoding="utf-8")
    if hashlib.sha256(body.encode()).hexdigest() != current["content_hash"]:
        raise ValueError("revision integrity error")
    return body


def save(
    conn, user_id: str, kind: str, body: str, expected: str | None, *, terms=(), origin="manual", source_chunk_id=None
) -> str:
    if kind not in KINDS or not body.strip() or len(body) > 24000 or "\x00" in body:
        raise ValueError("invalid profile")
    revision = uuid.uuid4().hex
    path = _path(user_id, kind, revision)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)
    try:
        with db.tx(conn):
            if (
                source_chunk_id
                and not conn.execute(
                    "SELECT 1 FROM note_chunks c JOIN note_versions n ON n.id=c.note_id WHERE c.id=? AND n.user_id=?",
                    (source_chunk_id, user_id),
                ).fetchone()
            ):
                raise RevisionConflict()
            current = head(conn, user_id, kind)
            if (current["id"] if current else None) != expected:
                raise RevisionConflict()
            conn.execute(
                "INSERT INTO profile_revisions VALUES (?,?,?,?,?,?,?,?)",
                (
                    revision,
                    user_id,
                    kind,
                    expected,
                    hashlib.sha256(body.encode()).hexdigest(),
                    json.dumps(list(terms)),
                    origin,
                    int(time.time()),
                ),
            )
            conn.execute("INSERT OR REPLACE INTO profile_heads VALUES (?,?,?)", (user_id, kind, revision))
            if source_chunk_id and kind == "public":
                db.kv_set(conn, "notes-in-feed-profile:" + user_id, "1")
            conn.execute("INSERT OR IGNORE INTO reader_settings(user_id) VALUES (?)", (user_id,))
            conn.execute("UPDATE reader_settings SET refresh_requested=1,version=version+1 WHERE user_id=?", (user_id,))
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return revision


def bootstrap(conn, did: str) -> str:
    uid = reader_id(conn, did)
    conn.execute("INSERT OR IGNORE INTO reader_settings(user_id) VALUES (?)", (uid,))
    if head(conn, uid):
        return uid
    user = conn.execute("SELECT prompt FROM users WHERE did=?", (did,)).fetchone()
    prefs = conn.execute("SELECT category, content FROM preferences WHERE user_did=? AND active=1", (did,)).fetchall()
    body = "# Social filtering profile\n\n## Goals\n" + ((user["prompt"] or "") if user else "")
    if prefs:
        body += "\n\n## Existing preferences\n" + "\n".join(f"- [{p['category']}] {p['content']}" for p in prefs)
    # Leave genuinely unconfigured readers pending; do not manufacture interests.
    if (user and user["prompt"].strip()) or prefs:
        blocks = [
            "bluesky:" + p[0]
            for p in conn.execute(
                "SELECT summary FROM preferences WHERE user_did=? AND active=1 AND category='author_block'", (did,)
            )
        ]
        if blocks:
            conn.execute("UPDATE reader_settings SET blocked_authors=? WHERE user_id=?", (json.dumps(blocks), uid))
        terms = [
            p[0]
            for p in conn.execute(
                "SELECT summary FROM preferences WHERE user_did=? AND active=1 AND category='search_term'", (did,)
            )
        ]
        try:
            save(conn, uid, "public", body, None, terms=terms, origin="legacy-import")
        except RevisionConflict:
            pass
    return uid
