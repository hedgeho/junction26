"""Read-only source snapshots and exact, owned source slices."""

import hashlib
import re
import time
import uuid

from curator_core import db

from . import jobs, profiles


def import_note(conn, user_id: str, title: str, text: str) -> str:
    if not title.strip() or len(title) > 200 or not text.strip() or len(text) > 200000 or "\x00" in text:
        raise ValueError("invalid note")
    nid = uuid.uuid4().hex
    with db.tx(conn):
        conn.execute(
            "INSERT INTO note_versions VALUES (?,?,?,?,?,?)",
            (nid, user_id, title, text, hashlib.sha256(text.encode()).hexdigest(), int(time.time())),
        )
        for start in range(0, len(text), 1000):
            chunk_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO note_chunks VALUES (?,?,?,?)", (chunk_id, nid, start, min(start + 1000, len(text)))
            )
            jobs.enqueue_note_chunk(conn, user_id, chunk_id)
        conn.execute("UPDATE reader_settings SET refresh_requested=1,version=version+1 WHERE user_id=?", (user_id,))
    return nid


def delete_note(conn, user_id: str, note_id: str) -> None:
    with db.tx(conn):
        if not conn.execute("SELECT 1 FROM note_versions WHERE id=? AND user_id=?", (note_id, user_id)).fetchone():
            return
        conn.execute(
            "DELETE FROM note_matches WHERE chunk_id IN (SELECT id FROM note_chunks WHERE note_id=?)", (note_id,)
        )
        conn.execute(
            "DELETE FROM feedback_jobs WHERE source_chunk_id IN (SELECT id FROM note_chunks WHERE note_id=?)",
            (note_id,),
        )
        conn.execute("DELETE FROM note_chunks WHERE note_id=?", (note_id,))
        conn.execute("DELETE FROM note_versions WHERE id=? AND user_id=?", (note_id, user_id))
        conn.execute("UPDATE reader_settings SET refresh_requested=1,version=version+1 WHERE user_id=?", (user_id,))


def source_chunks(conn, user_id: str) -> list[dict]:
    rows = conn.execute(
        """SELECT c.*,n.title,n.text,n.content_hash FROM note_chunks c
        JOIN note_versions n ON n.id=c.note_id WHERE n.user_id=? ORDER BY n.created_at DESC,c.start_offset""",
        (user_id,),
    )
    out = []
    for row in rows:
        if hashlib.sha256(row["text"].encode()).hexdigest() != row["content_hash"]:
            continue
        out.append(
            {
                "chunk_id": row["id"],
                "note_id": row["note_id"],
                "title": row["title"],
                "text": row["text"][row["start_offset"] : row["end_offset"]],
            }
        )
    return out


def match_post(conn, user_id: str, post, gemma) -> None:
    chunks = source_chunks(conn, user_id)
    if not chunks:
        return
    words = set(re.findall(r"\w{4,}", post.text.lower()))
    chunks.sort(key=lambda c: -len(words & set(re.findall(r"\w{4,}", c["text"].lower()))))
    short = chunks[:12]
    current = profiles.head(conn, user_id, "private")
    private_revision = current["id"] if current else ""
    result = gemma.match(
        {"text": post.text, "context": post.context}, short, profiles.markdown(conn, user_id, "private")
    )
    allowed = {c["chunk_id"] for c in short}
    ids = [m.chunk_id for m in result.items]
    if len(ids) != len(set(ids)) or not set(ids) <= allowed:
        raise ValueError("invalid match")
    with db.tx(conn):
        for match in result.items:
            if (
                match.relevance >= 0.7
                and conn.execute("SELECT 1 FROM note_chunks WHERE id=?", (match.chunk_id,)).fetchone()
            ):
                conn.execute(
                    "INSERT OR REPLACE INTO note_matches VALUES (?,?,?,?,?,?)",
                    (user_id, post.key, post.content_hash, private_revision, match.chunk_id, match.relevance),
                )


def matches_for(conn, user_id: str, post) -> list[dict]:
    current = profiles.head(conn, user_id, "private")
    revision = current["id"] if current else ""
    ids = {
        r[0]
        for r in conn.execute(
            """SELECT chunk_id FROM note_matches
        WHERE user_id=? AND post_key=? AND content_hash=? AND private_revision=? ORDER BY relevance DESC LIMIT 3""",
            (user_id, post.key, post.content_hash, revision),
        )
    }
    return [c for c in source_chunks(conn, user_id) if c["chunk_id"] in ids]
