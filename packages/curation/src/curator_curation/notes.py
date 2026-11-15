"""Read-only source snapshots and exact, owned source slices."""

import hashlib
import json
import logging
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext

from curator_core import db

from . import jobs, profiles
from .config import settings

log = logging.getLogger(__name__)


def import_note(conn, user_id: str, title: str, text: str) -> str:
    if not title.strip() or len(title) > 200 or not text.strip() or len(text) > 200000 or "\x00" in text:
        raise ValueError("invalid note")
    nid = uuid.uuid4().hex
    with nullcontext() if conn.in_transaction else db.tx(conn):
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
    with nullcontext() if conn.in_transaction else db.tx(conn):
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
    documents = {
        row["id"]: row
        for row in conn.execute("SELECT id,title,text,content_hash FROM note_versions WHERE user_id=?", (user_id,))
        if hashlib.sha256(row["text"].encode()).hexdigest() == row["content_hash"]
    }
    rows = conn.execute(
        """SELECT c.* FROM note_chunks c
        JOIN note_versions n ON n.id=c.note_id WHERE n.user_id=? ORDER BY n.created_at DESC,c.start_offset""",
        (user_id,),
    )
    out = []
    for row in rows:
        document = documents.get(row["note_id"])
        if not document:
            continue
        out.append(
            {
                "chunk_id": row["id"],
                "note_id": row["note_id"],
                "title": document["title"],
                "text": document["text"][row["start_offset"] : row["end_offset"]],
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
    return [
        {
            "chunk_id": row["id"],
            "note_id": row["note_id"],
            "title": row["title"],
            "text": row["text"][row["start_offset"] : row["end_offset"]],
        }
        for row in conn.execute(
            """SELECT c.*,n.title,n.text,n.content_hash FROM note_matches m
            JOIN note_chunks c ON c.id=m.chunk_id JOIN note_versions n ON n.id=c.note_id
            WHERE m.user_id=? AND n.user_id=? AND m.post_key=? AND m.content_hash=? AND m.private_revision=?
            ORDER BY m.relevance DESC,c.id LIMIT 3""",
            (user_id, user_id, post.key, post.content_hash, revision),
        )
        if hashlib.sha256(row["text"].encode()).hexdigest() == row["content_hash"]
    ]


def match_feed(conn, user_id, run_id, posts, gemma):
    cfg = settings()
    chunks = source_chunks(conn, user_id)
    head = profiles.head(conn, user_id, "private")
    revision = head["id"] if head else ""
    profile = profiles.markdown(conn, user_id, "private")
    words = [set(re.findall(r"\w{4,}", post.text.lower())) for post in posts]
    grouped = {}
    for chunk in chunks:
        tokens = set(re.findall(r"\w{4,}", (chunk["title"] + " " + chunk["text"]).lower()))
        score = max((len(tokens & post_words) for post_words in words), default=0)
        grouped.setdefault(chunk["note_id"], []).append((score, chunk))
    ranked_notes = sorted(grouped.values(), key=lambda items: -max(score for score, _ in items))
    text_limit = min(4000, 18000 // max(1, len(posts)))
    public_posts = [
        {"post_key": post.key, "text": post.text[:text_limit], "context": post.context[: text_limit // 4]}
        for post in posts
    ]
    work = []
    exhausted = 0
    conn.execute(
        "DELETE FROM note_match_batches WHERE user_id=? AND updated_at<?", (user_id, int(time.time()) - 7 * 86400)
    )
    for items in ranked_notes[: max(1, min(50, cfg.note_match_top_notes))]:
        short = [
            chunk
            for _, chunk in sorted(items, key=lambda item: (-item[0], item[1]["chunk_id"]))[
                : max(1, min(12, cfg.note_match_chunks_per_note))
            ]
        ]
        short.sort(key=lambda chunk: chunk["chunk_id"])
        pending = []
        for post, public_post in zip(posts, public_posts, strict=True):
            # Cache both positive and empty decisions per note/post, so a single new post
            # does not send the unchanged selection through inference again.
            cache_key = hashlib.sha256(
                json.dumps(
                    ["note-pair-v2", revision, post.key, post.content_hash, short, getattr(gemma, "model", "")],
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            cached = conn.execute(
                "SELECT state,attempts FROM note_match_batches WHERE user_id=? AND cache_key=?", (user_id, cache_key)
            ).fetchone()
            if not cached or (cached["state"] != "ready" and cached["attempts"] < 3):
                pending.append((cache_key, public_post))
            elif cached["state"] != "ready":
                exhausted += 1
        if pending:
            work.append((pending, short))
    progress = {"run_id": run_id, "state": "processing", "completed": 0, "total": len(work)}
    key = "note-matching:" + user_id
    db.kv_set(conn, key, json.dumps(progress))
    failures = exhausted
    retryable = False
    matched = 0
    with ThreadPoolExecutor(max_workers=max(1, min(16, cfg.note_match_concurrency))) as pool:
        futures = {}
        for pending, short in work:
            for cache_key, _ in pending:
                conn.execute(
                    "INSERT INTO note_match_batches VALUES (?,?,'processing',1,?) ON CONFLICT(user_id,cache_key) DO UPDATE SET state='processing',attempts=attempts+1,updated_at=excluded.updated_at",
                    (user_id, cache_key, int(time.time())),
                )
            futures[pool.submit(gemma.match_note, [post for _, post in pending], short, profile)] = (pending, short)
        for future in as_completed(futures):
            pending, short = futures[future]
            try:
                result = future.result()
                with db.tx(conn):
                    current = profiles.head(conn, user_id, "private")
                    if (current["id"] if current else "") != revision:
                        raise ValueError("profile changed during matching")
                    allowed = {chunk["chunk_id"] for chunk in short}
                    requested = {post["post_key"] for _, post in pending}
                    by_post = {post.key: post for post in posts if post.key in requested}
                    seen = set()
                    for item in result.items:
                        if item.post_key not in by_post or item.chunk_id not in allowed or item.post_key in seen:
                            raise ValueError("invalid note match")
                        seen.add(item.post_key)
                        owned = conn.execute(
                            "SELECT 1 FROM note_chunks c JOIN note_versions n ON n.id=c.note_id WHERE c.id=? AND n.user_id=?",
                            (item.chunk_id, user_id),
                        ).fetchone()
                        current_post = conn.execute(
                            "SELECT 1 FROM digest_items WHERE run_id=? AND post_key=? AND content_hash=?",
                            (run_id, item.post_key, by_post[item.post_key].content_hash),
                        ).fetchone()
                        if owned and current_post and item.relevance >= 0.7:
                            conn.execute(
                                "INSERT OR REPLACE INTO note_matches VALUES (?,?,?,?,?,?)",
                                (
                                    user_id,
                                    item.post_key,
                                    by_post[item.post_key].content_hash,
                                    revision,
                                    item.chunk_id,
                                    item.relevance,
                                ),
                            )
                            matched += 1
                    conn.executemany(
                        "UPDATE note_match_batches SET state='ready' WHERE user_id=? AND cache_key=?",
                        [(user_id, cache_key) for cache_key, _ in pending],
                    )
            except Exception:  # noqa: BLE001
                failures += 1
                conn.executemany(
                    "UPDATE note_match_batches SET state='failed' WHERE user_id=? AND cache_key=?",
                    [(user_id, cache_key) for cache_key, _ in pending],
                )
                log.warning("Private note matching failed; feed remains available")
                retryable = retryable or any(
                    conn.execute(
                        "SELECT attempts FROM note_match_batches WHERE user_id=? AND cache_key=?", (user_id, cache_key)
                    ).fetchone()["attempts"]
                    < 3
                    for cache_key, _ in pending
                )
            progress["completed"] += 1
            db.kv_set(conn, key, json.dumps(progress))
    progress["state"] = "retrying" if retryable else "partial" if failures else "ready"
    db.kv_set(conn, key, json.dumps(progress))
    return matched
