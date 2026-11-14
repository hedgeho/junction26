"""Turn scores into the materialized feed_items list that getFeedSkeleton serves."""

import math
import time

from curator_core import db

from .config import settings
from .readers import prefs_of

GLOBAL = "__global__"  # fallback feed for requesters who haven't set up a profile
FEED_WINDOW_H = 48
HALF_LIFE_H = 10
MIN_SCORE = 5


def _write_feed(conn, did: str, ranked: list[tuple[str, str | None]]) -> None:
    with db.tx(conn):
        conn.execute("DELETE FROM feed_items WHERE user_did = ?", (did,))
        conn.executemany(
            "INSERT INTO feed_items VALUES (?, ?, ?, ?)",
            [(did, pos, uri, reason) for pos, (uri, reason) in enumerate(ranked)],
        )


def _diversify(items: list[dict], size: int) -> list[dict]:
    """Greedy pick with a per-author penalty so one prolific account can't flood the feed."""
    per_author: dict[str, int] = {}
    pool = sorted(items, key=lambda x: -x["value"])
    out = []
    while pool and len(out) < size:
        best = max(pool[:50], key=lambda x: x["value"] * 0.6 ** per_author.get(x["author_did"], 0))
        pool.remove(best)
        per_author[best["author_did"]] = per_author.get(best["author_did"], 0) + 1
        out.append(best)
    return out


def rank_user(conn, did: str) -> int:
    now = time.time()
    boosted = set(prefs_of(conn, did, "author_boost"))
    less_posts = {
        r["post_uri"]
        for r in conn.execute(
            "SELECT post_uri FROM interactions WHERE user_did = ? AND event LIKE '%requestLess'", (did,)
        )
    }
    rows = conn.execute(
        """SELECT s.post_uri, s.score, s.reason, p.author_did, p.indexed_at, p.likes, p.reposts
           FROM scores s JOIN posts p ON p.uri = s.post_uri
           WHERE s.user_did = ? AND s.score >= ? AND p.indexed_at > ?""",
        (did, MIN_SCORE / 10, int(now) - FEED_WINDOW_H * 3600),
    ).fetchall()
    items = []
    for r in rows:
        if r["post_uri"] in less_posts:
            continue
        age_h = (now - r["indexed_at"]) / 3600
        value = r["score"] * 0.5 ** (age_h / HALF_LIFE_H) * (1 + 0.05 * math.log1p(r["likes"] + r["reposts"]))
        if r["author_did"] in boosted:
            value *= 1.3
        items.append(dict(r) | {"value": value})
    ranked = _diversify(items, settings().feed_size)
    _write_feed(conn, did, [(x["post_uri"], x["reason"]) for x in ranked])
    return len(ranked)


def rank_global(conn) -> int:
    """No-LLM fallback: recent, engaged, top-level posts."""
    now = int(time.time())
    rows = conn.execute(
        """SELECT uri AS post_uri, author_did, indexed_at, likes, reposts FROM posts
           WHERE indexed_at > ? AND reply_parent IS NULL AND likes + 2 * reposts >= 25
           ORDER BY likes + 2 * reposts DESC LIMIT 2000""",
        (now - 12 * 3600,),
    ).fetchall()
    items = [
        dict(r) | {"value": math.log1p(r["likes"] + 2 * r["reposts"]) * 0.5 ** ((now - r["indexed_at"]) / 3600 / 4)}
        for r in rows
    ]
    ranked = _diversify(items, settings().feed_size)
    _write_feed(conn, GLOBAL, [(x["post_uri"], None) for x in ranked])
    return len(ranked)
