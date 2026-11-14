"""Curation worker: candidates -> LLM scores -> ranked feed, per reader, on a loop.

Nothing here runs in the request path. getFeedSkeleton only reads feed_items,
so a slow or failing LLM call means a stale feed, never a slow one.
"""

import logging
import time

import anthropic
from curator_core import db

from .candidates import candidates
from .config import settings
from .llm import scorer
from .rank import rank_global, rank_user
from .readers import active_preferences

log = logging.getLogger(__name__)


def eligible_users(conn) -> list[str]:
    week_ago = int(time.time()) - 7 * 86400
    rows = conn.execute("SELECT did FROM users WHERE prompt != '' AND last_seen_at > ?", (week_ago,)).fetchall()
    return [r["did"] for r in rows]


def drop_orphan_scores(conn) -> None:
    """Scores for posts ingest has pruned or seen deleted."""
    with db.tx(conn):
        conn.execute("DELETE FROM scores WHERE NOT EXISTS (SELECT 1 FROM posts p WHERE p.uri = scores.post_uri)")


def feedback_examples(conn, did: str, event_suffix: str, n: int = 8) -> list[str]:
    rows = conn.execute(
        """SELECT p.text FROM interactions i JOIN posts p ON p.uri = i.post_uri
           WHERE i.user_did = ? AND i.event LIKE ? ORDER BY i.created_at DESC LIMIT ?""",
        (did, f"%{event_suffix}", n),
    ).fetchall()
    return [r["text"] for r in rows]


def score_user(conn, client: anthropic.Anthropic, did: str) -> int:
    cfg = settings()
    user = conn.execute("SELECT prompt FROM users WHERE did = ?", (did,)).fetchone()
    prefs = [dict(p) for p in active_preferences(conn, did) if p["category"] != "search_term"]
    profile = scorer.profile_block(
        user["prompt"],
        prefs,
        more=feedback_examples(conn, did, "requestMore"),
        less=feedback_examples(conn, did, "requestLess"),
    )
    followed = {r["subject_did"] for r in conn.execute("SELECT subject_did FROM follows WHERE user_did = ?", (did,))}
    posts = candidates(conn, did, cfg.candidates_per_cycle)
    now = int(time.time())
    scored = 0
    for i in range(0, len(posts), cfg.score_batch_size):
        batch = posts[i : i + cfg.score_batch_size]
        try:
            results = scorer.score_batch(client, profile, batch, followed)
        except anthropic.APIError as e:
            log.error("scoring failed for %s: %s", did, e)
            break
        with db.tx(conn):
            conn.executemany(
                "INSERT OR REPLACE INTO scores VALUES (?, ?, ?, ?, ?, ?)",
                [(did, batch[r.i]["uri"], r.score / 10, r.reason, cfg.scorer_model, now) for r in results],
            )
        scored += len(results)
    return scored


def run_cycle(conn, client: anthropic.Anthropic) -> None:
    drop_orphan_scores(conn)
    log.info("global fallback feed: %d items", rank_global(conn))
    for did in eligible_users(conn):
        try:
            n = score_user(conn, client, did)
            m = rank_user(conn, did)
            log.info("%s: scored %d new, feed has %d", did, n, m)
        except Exception:
            log.exception("curation failed for %s", did)


def run_forever() -> None:
    cfg = settings()
    conn = db.connect()
    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key or None)
    while True:
        started = time.monotonic()
        run_cycle(conn, client)
        time.sleep(max(0, cfg.curate_interval_s - (time.monotonic() - started)))
