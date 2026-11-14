"""Cheap recall: which posts are worth spending an LLM call on for this reader."""

import logging
import math
import time

from curator_core.rows import Post

from .readers import prefs_of

log = logging.getLogger(__name__)

CANDIDATE_WINDOW_H = 24


def _fts_query(terms: list[str]) -> str:
    return " OR ".join('"' + t.replace('"', "") + '"' for t in terms if t.strip())


def candidates(conn, did: str, limit: int) -> list[Post]:
    """Union of three cheap recall sources, deduped, minus already-scored posts."""
    since = int(time.time()) - CANDIDATE_WINDOW_H * 3600
    blocked = set(prefs_of(conn, did, "author_block"))
    unscored = "NOT EXISTS (SELECT 1 FROM scores s WHERE s.user_did = :did AND s.post_uri = p.uri)"
    args = {"did": did, "since": since}

    sources: list[tuple[str, str, dict]] = [
        (
            "follows",
            f"""
            SELECT p.* FROM posts p JOIN follows f ON f.subject_did = p.author_did AND f.user_did = :did
            WHERE p.indexed_at > :since AND {unscored}
            ORDER BY p.indexed_at DESC LIMIT :n""",
            {},
        ),
        (
            "popular",
            f"""
            SELECT p.* FROM posts p
            WHERE p.indexed_at > :since AND p.reply_parent IS NULL
              AND p.likes + 2 * p.reposts >= 25 AND {unscored}
            ORDER BY p.likes + 2 * p.reposts DESC LIMIT :n""",
            {},
        ),
    ]
    terms = prefs_of(conn, did, "search_term")
    if terms:
        sources.insert(
            1,
            (
                "search",
                f"""
            SELECT p.* FROM posts_fts JOIN posts p ON p.rowid = posts_fts.rowid
            WHERE posts_fts MATCH :q AND p.indexed_at > :since AND {unscored}
            ORDER BY bm25(posts_fts) LIMIT :n""",
                {"q": _fts_query(terms)},
            ),
        )

    seen: dict[str, Post] = {}
    share = math.ceil(limit / len(sources))
    for name, sql, extra in sources:
        rows = conn.execute(sql, args | extra | {"n": share * 2}).fetchall()
        added = 0
        for r in rows:
            if added >= share or len(seen) >= limit:
                break
            if r["uri"] in seen or r["author_did"] in blocked:
                continue
            if len(r["text"]) < 15 and not r["embed_kind"]:
                continue
            seen[r["uri"]] = Post(**r)
            added += 1
        log.debug("%s: %d candidates from %s", did, added, name)
    return list(seen.values())
