"""Finite daily snapshots. Only source text can cross the reader boundary."""

import json
import logging
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from curator_core import db
from curator_core.sources import get_post

from . import notes, profiles
from .config import settings

log = logging.getLogger(__name__)


def day_key(config) -> str:
    return datetime.now(ZoneInfo(config["timezone"])).date().isoformat()


def config_for(conn, uid):
    return conn.execute("SELECT * FROM reader_settings WHERE user_id=?", (uid,)).fetchone()


def latest(conn, uid):
    return conn.execute(
        "SELECT * FROM digest_runs WHERE user_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (uid,)
    ).fetchone()


def blocked(config) -> set[str]:
    return set(json.loads(config["blocked_authors"]))


def build(conn, uid, scorer_version: str, gemma=None) -> str | None:
    profile = profiles.head(conn, uid)
    config = config_for(conn, uid)
    if not profile or not config:
        return None
    rows = conn.execute(
        """SELECT * FROM assessments_v2 WHERE user_id=? AND profile_revision=? AND scorer_version=?
        AND decision='include' AND relevance>=?""",
        (uid, profile["id"], scorer_version, settings().minimum_relevance),
    ).fetchall()
    dismissed = {r[0] for r in conn.execute("SELECT post_key FROM dismissed_posts WHERE user_id=?", (uid,))}
    previous = {
        r[0]
        for r in conn.execute(
            """SELECT i.post_key FROM digest_items i JOIN digest_runs d ON d.id=i.run_id
        WHERE d.user_id=? AND d.day<>?""",
            (uid, day_key(config)),
        )
    }
    pool = []
    for row in rows:
        post = get_post(conn, row["post_key"])
        if (
            not post
            or post.content_hash != row["content_hash"]
            or post.author_key in blocked(config)
            or post.key in dismissed | previous
            or post.observed_at < time.time() - 86400
        ):
            continue
        age_h = max(0, (time.time() - post.published_at) / 3600)
        value = (0.65 * row["relevance"] + 0.25 * row["substance"] + 0.1 * row["opportunity"]) * 0.5 ** (age_h / 48)
        pool.append((value, post))
    pool.sort(key=lambda p: (-p[0], p[1].key))
    selected, authors, conversations, texts = [], {}, set(), set()
    for _, post in pool:
        conversation = (post.platform, post.root_id or post.parent_id or post.native_id)
        normalized = " ".join(post.text.casefold().split())
        if conversation in conversations or normalized in texts or authors.get(post.author_key, 0) >= 2:
            continue
        selected.append(post)
        conversations.add(conversation)
        texts.add(normalized)
        authors[post.author_key] = authors.get(post.author_key, 0) + 1
        if len(selected) >= config["daily_limit"]:
            break
    if gemma:
        for post in selected:
            try:
                notes.match_post(conn, uid, post, gemma)
            except Exception:  # noqa: BLE001 - isolate provider failure without leaking its private payload
                log.warning("Private matching failed; source selection remains available")
    run_id = uuid.uuid4().hex
    with db.tx(conn):
        current = profiles.head(conn, uid)
        current_config = config_for(conn, uid)
        if current["id"] != profile["id"] or current_config["version"] != config["version"]:
            return None
        conn.execute(
            "INSERT INTO digest_runs VALUES (?,?,?,?,?,?)",
            (run_id, uid, day_key(config), profile["id"], config["version"], int(time.time())),
        )
        conn.executemany(
            "INSERT INTO digest_items VALUES (?,?,?,?)",
            [(run_id, i, p.key, p.content_hash) for i, p in enumerate(selected)],
        )
        conn.execute("UPDATE reader_settings SET refresh_requested=0 WHERE user_id=?", (uid,))
    return run_id


def read(conn, uid) -> list[dict]:
    run = latest(conn, uid)
    profile = profiles.head(conn, uid)
    config = config_for(conn, uid)
    if not run or not profile or run["profile_revision"] != profile["id"]:
        return []
    dismissed = {r[0] for r in conn.execute("SELECT post_key FROM dismissed_posts WHERE user_id=?", (uid,))}
    out = []
    for item in conn.execute("SELECT * FROM digest_items WHERE run_id=? ORDER BY position", (run["id"],)):
        post = get_post(conn, item["post_key"])
        if (
            not post
            or post.key in dismissed
            or post.content_hash != item["content_hash"]
            or post.author_key in blocked(config)
        ):
            continue
        # Explicit source-only DTO. Never expose assessment/profile/generated metadata.
        out.append(
            {
                "key": post.key,
                "platform": post.platform,
                "author": post.author_name,
                "text": post.text,
                "url": post.canonical_url,
                "created_at": post.created_at,
                "notes": notes.matches_for(conn, uid, post),
            }
        )
    return out[: config["daily_limit"]]
