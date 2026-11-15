"""Cheap recall: which posts are worth spending an LLM call on for this reader."""

import json
import logging
import math
import time

from curator_core import db
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


RISING_KEY = "rising-posts"
RISING_TTL_S = 300
RISING_MIN_ENGAGEMENT = 5
RISING_KEEP = 5000


def rising(conn, limit: int) -> list[str]:
    """Top-level posts gaining engagement fastest: (likes + 2·reposts + replies) / (age_h + 2)^1.5.

    Shared by all readers and recomputed at most every few minutes.
    """
    now = int(time.time())
    cached = db.kv_get(conn, RISING_KEY)
    if cached:
        entry = json.loads(cached)
        if now - entry["at"] < RISING_TTL_S:
            return entry["keys"][:limit]
    rows = conn.execute(
        """SELECT uri, likes + 2 * reposts + replies, indexed_at FROM posts
        WHERE indexed_at>? AND reply_parent IS NULL AND likes + 2 * reposts + replies >= ?""",
        (now - CANDIDATE_WINDOW_H * 3600, RISING_MIN_ENGAGEMENT),
    ).fetchall()
    ranked = sorted(rows, key=lambda r: r[1] / ((now - r[2]) / 3600 + 2) ** 1.5, reverse=True)
    keys = ["bluesky:" + r[0] for r in ranked[:RISING_KEEP]]
    db.kv_set(conn, RISING_KEY, json.dumps({"at": now, "keys": keys}))
    return keys[:limit]


def public_candidates(conn, did: str, terms: list[str], limit: int):
    """Recall follows and explicit topics across the full source window, plus discovery.

    Crucially, followed/topic posts are not first restricted to a recent firehose slice.
    Private interests/notes must never be passed as terms here.
    """
    from curator_core.sources import get_post, recent_posts

    since = int(time.time()) - CANDIDATE_WINDOW_H * 3600
    share = max(1, math.ceil(limit / 3))
    followed = conn.execute(
        """SELECT p.uri FROM posts p JOIN follows f ON f.subject_did=p.author_did
        WHERE f.user_did=? AND p.indexed_at>? ORDER BY p.indexed_at DESC LIMIT ?""",
        (did, since, share),
    )
    pools = [["bluesky:" + r[0] for r in followed]]
    endorsed = conn.execute(
        """SELECT e.subject_uri FROM followed_engagement e
        JOIN follows f ON f.subject_did=e.actor_did AND f.user_did=?
        WHERE e.at>? AND e.subject_uri NOT LIKE ? GROUP BY e.subject_uri
        ORDER BY count(DISTINCT e.actor_did) DESC, max(e.at) DESC LIMIT ?""",
        (did, since, f"at://{did}/%", share),
    )
    pools.append(["bluesky:" + r[0] for r in endorsed])
    for_you = conn.execute(
        """SELECT post_key FROM bluesky_feed_posts WHERE user_did=? AND observed_at>?
        ORDER BY observed_at DESC, position LIMIT ?""",
        (did, since, share),
    )
    pools.append([r[0] for r in for_you])
    query = _fts_query(terms)
    if query:
        matched = conn.execute(
            """SELECT p.uri FROM posts_fts JOIN posts p ON p.rowid=posts_fts.rowid
            WHERE posts_fts MATCH ? AND p.indexed_at>? ORDER BY bm25(posts_fts) LIMIT ?""",
            (query, since, share),
        )
        pools.append(["bluesky:" + r[0] for r in matched])
        normalized = conn.execute(
            """SELECT p.platform,p.native_id FROM social_posts_fts
            JOIN social_posts p ON p.rowid=social_posts_fts.rowid
            WHERE social_posts_fts MATCH ? AND p.observed_at>? AND p.deleted=0
            ORDER BY bm25(social_posts_fts) LIMIT ?""",
            (query, since, share),
        )
        pools.append([f"{r[0]}:{r[1]}" for r in normalized])
    pools.append(rising(conn, share))
    # Recent posts backfill unused quota, while deterministic dedup preserves source identity.
    pools.append([p.key for p in recent_posts(conn, since, limit)])
    seen = {}
    for pool in pools:
        for key in pool:
            if len(seen) >= limit:
                break
            if key not in seen and (post := get_post(conn, key)) is not None:
                seen[key] = post
    return list(seen.values())
