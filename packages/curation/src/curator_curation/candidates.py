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


def public_candidates(conn, did: str, terms: list[str], limit: int):
    """Recall follows and explicit topics across the full source window, plus discovery.

    Crucially, followed/topic posts are not first restricted to a recent firehose slice.
    Private interests/notes must never be passed as terms here.
    """
    from curator_core.sources import get_post, recent_posts

    since = int(time.time()) - CANDIDATE_WINDOW_H * 3600
    # A connected reader's Bluesky recall comes exclusively from their For You
    # snapshot. Other readers' memberships never become this reader's candidates.
    connected = conn.execute("SELECT 1 FROM bluesky_feed_sync WHERE user_did=?", (did,)).fetchone()
    if connected:
        feed_rows = conn.execute(
            """SELECT post_key FROM bluesky_feed_posts WHERE user_did=? AND observed_at>?
            ORDER BY position LIMIT ?""",
            (did, since, limit),
        )
        feed_posts = [post for row in feed_rows if (post := get_post(conn, row[0])) is not None]
        x_rows = conn.execute(
            """SELECT native_id FROM social_posts WHERE platform='x' AND deleted=0 AND observed_at>?
            ORDER BY observed_at DESC LIMIT ?""",
            (since, limit),
        )
        x_posts = [post for row in x_rows if (post := get_post(conn, "x:" + row[0])) is not None]
        # Interleave sources so one platform cannot fill the entire scoring budget.
        result = []
        for i in range(max(len(feed_posts), len(x_posts))):
            if i < len(feed_posts):
                result.append(feed_posts[i])
            if i < len(x_posts):
                result.append(x_posts[i])
        return result[:limit]
    share = max(1, math.ceil(limit / 3))
    followed = conn.execute(
        """SELECT p.uri FROM posts p JOIN follows f ON f.subject_did=p.author_did
        WHERE f.user_did=? AND p.indexed_at>? ORDER BY p.indexed_at DESC LIMIT ?""",
        (did, since, share),
    )
    pools = [["bluesky:" + r[0] for r in followed]]
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
    # Discovery backfills unused quota, while deterministic dedup preserves source identity.
    pools.append([p.key for p in recent_posts(conn, since, limit)])
    seen = {}
    for pool in pools:
        for key in pool:
            if len(seen) >= limit:
                break
            if key not in seen and (post := get_post(conn, key)) is not None:
                seen[key] = post
    return list(seen.values())
