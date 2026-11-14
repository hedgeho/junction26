"""Finite daily snapshots. Only source text can cross the reader boundary."""

import json
import logging
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from curator_core import db, media
from curator_core.sources import get_post

from . import notes, profiles, screen, triage
from .candidates import public_candidates
from .config import settings
from .llm.gemini import DECISION_SCORER_VERSION

log = logging.getLogger(__name__)
BASELINE_REVISION = "baseline-v1"


def revision(profile):
    return profile["id"] if profile else BASELINE_REVISION


def day_key(config) -> str:
    return datetime.now(ZoneInfo(config["timezone"])).date().isoformat()


def delivery_limit(config) -> int:
    extra = config["requested_more_count"] if config["requested_more_day"] == day_key(config) else 0
    return min(200, config["daily_limit"] + extra)


def config_for(conn, uid):
    return conn.execute("SELECT * FROM reader_settings WHERE user_id=?", (uid,)).fetchone()


def latest(conn, uid):
    return conn.execute(
        "SELECT * FROM digest_runs WHERE user_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (uid,)
    ).fetchone()


def blocked(config) -> set[str]:
    return set(json.loads(config["blocked_authors"]))


def build(conn, uid, scorer_version: str) -> str | None:
    profile = profiles.head(conn, uid)
    config = config_for(conn, uid)
    if not config:
        return None
    decisions = scorer_version.startswith(DECISION_SCORER_VERSION)
    threshold = settings().decision_minimum_relevance if decisions else settings().minimum_relevance
    rows = conn.execute(
        """SELECT * FROM assessments_v2 WHERE user_id=? AND profile_revision=? AND scorer_version=?
        AND decision='include' AND relevance>=?""",
        (uid, revision(profile), scorer_version, threshold),
    ).fetchall()
    # Substantive posts the reader did not ask for but that are not unrelated; never flagged ones.
    nearby = (
        conn.execute(
            """SELECT * FROM assessments_v2 WHERE user_id=? AND profile_revision=? AND scorer_version=?
            AND decision='exclude' AND relevance>=0.5 AND substance>=0.75""",
            (uid, revision(profile), scorer_version),
        ).fetchall()
        if decisions
        else []
    )
    dismissed = {r[0] for r in conn.execute("SELECT post_key FROM dismissed_posts WHERE user_id=?", (uid,))}
    previous = {
        r[0]
        for r in conn.execute(
            """SELECT i.post_key FROM digest_items i JOIN digest_runs d ON d.id=i.run_id
        WHERE d.user_id=? AND d.day<>?""",
            (uid, day_key(config)),
        )
    }
    account = conn.execute("SELECT bluesky_did FROM reader_accounts WHERE id=?", (uid,)).fetchone()
    followed = {
        "bluesky:" + r[0]
        for r in conn.execute(
            "SELECT subject_did FROM follows WHERE user_did=?", (account["bluesky_did"] if account else "",)
        )
    }

    def candidates(assessments, strict):
        out = []
        for row in assessments:
            post = get_post(conn, row["post_key"])
            if (
                not post
                or post.content_hash != row["content_hash"]
                or post.author_key in blocked(config)
                or post.key in dismissed | previous
                or post.observed_at < time.time() - 86400
                or screen.label_blocked(screen.labels_of(conn, post.key))
            ):
                continue
            flags = triage.flags_of(conn, post)
            if triage.hidden(flags) or (strict and triage.penalty(flags) < 1):
                continue
            age_h = max(0, (time.time() - post.published_at) / 3600)
            value = (0.65 * row["relevance"] + 0.25 * row["substance"] + 0.1 * row["opportunity"]) * 0.5 ** (age_h / 48)
            out.append((value * triage.penalty(flags), post))
        out.sort(key=lambda p: (-p[0], p[1].key))
        return out

    pool = candidates(rows, strict=False)
    limit = delivery_limit(config)
    selected, authors, conversations, texts, stories = [], {}, set(), set(), set()

    def take(post) -> bool:
        conversation = (post.platform, post.root_id or post.parent_id or post.native_id)
        normalized = " ".join(post.text.casefold().split())
        story = screen.story_key(post)
        if (
            post in selected
            or conversation in conversations
            or normalized in texts
            or (story and story in stories)
            or authors.get(post.author_key, 0) >= 2
        ):
            return False
        selected.append(post)
        conversations.add(conversation)
        texts.add(normalized)
        if story:
            stories.add(story)
        authors[post.author_key] = authors.get(post.author_key, 0) + 1
        return True

    social = 0
    for _, post in pool:
        if social >= min(settings().social_slots, limit):
            break
        if post.author_key in followed and take(post):
            social += 1
    serendipity = candidates(nearby, strict=True)
    reserved = min(len(serendipity), int(limit * settings().serendipity_share))
    for _, post in pool:
        if len(selected) >= limit - reserved:
            break
        take(post)
    for _, post in serendipity:
        if len(selected) >= limit:
            break
        take(post)
    rank = {post.key: i for i, (_, post) in enumerate(pool)}
    selected.sort(key=lambda p: rank.get(p.key, len(rank)))
    run_id = uuid.uuid4().hex
    with db.tx(conn):
        current = profiles.head(conn, uid)
        current_config = config_for(conn, uid)
        if revision(current) != revision(profile) or current_config["version"] != config["version"]:
            return None
        conn.execute(
            "INSERT INTO digest_runs VALUES (?,?,?,?,?,?)",
            (run_id, uid, day_key(config), revision(profile), config["version"], int(time.time())),
        )
        conn.executemany(
            "INSERT INTO digest_items VALUES (?,?,?,?)",
            [(run_id, i, p.key, p.content_hash) for i, p in enumerate(selected)],
        )
        conn.execute("UPDATE reader_settings SET refresh_requested=0 WHERE user_id=?", (uid,))
    return run_id


def match_notes(conn, uid, run_id, gemma) -> int:
    """Runs after the digest is published; matches appear on the next read."""
    matched = 0
    for item in conn.execute(
        "SELECT post_key FROM digest_items WHERE run_id=? ORDER BY position", (run_id,)
    ).fetchall():
        post = get_post(conn, item["post_key"])
        if not post:
            continue
        try:
            notes.match_post(conn, uid, post, gemma)
            matched += 1
        except Exception:  # noqa: BLE001 - isolate provider failure without leaking its private payload
            log.warning("Private matching failed; source selection remains available")
    return matched


def read(conn, uid) -> list[dict]:
    run = latest(conn, uid)
    profile = profiles.head(conn, uid)
    config = config_for(conn, uid)
    if not config:
        return []
    if not run or run["profile_revision"] != revision(profile) or config["refresh_requested"]:
        return initial_feed(conn, uid, config)
    dismissed = {r[0] for r in conn.execute("SELECT post_key FROM dismissed_posts WHERE user_id=?", (uid,))}
    posts = []
    for item in conn.execute("SELECT * FROM digest_items WHERE run_id=? ORDER BY position", (run["id"],)):
        post = get_post(conn, item["post_key"])
        if (
            not post
            or post.key in dismissed
            or post.content_hash != item["content_hash"]
            or post.author_key in blocked(config)
        ):
            continue
        posts.append(post)
    return present(conn, uid, posts)[: delivery_limit(config)]


def present(conn, uid, posts) -> list[dict]:
    out = []
    for post in posts:
        # Explicit source-only DTO. Never expose assessment/profile/generated metadata.
        out.append(
            {
                "key": post.key,
                "platform": post.platform,
                "author": post.author_name,
                "author_handle": post.author_handle,
                "author_avatar": post.author_avatar,
                "media": media.display(post.media, post.canonical_url),
                "facets": post.facets,
                "text": post.text,
                "url": post.canonical_url,
                "created_at": post.created_at,
                "notes": notes.matches_for(conn, uid, post),
            }
        )
    return out


def initial_feed(conn, uid, config) -> list[dict]:
    account = conn.execute("SELECT bluesky_did FROM reader_accounts WHERE id=?", (uid,)).fetchone()
    if not account or not config:
        return []
    dismissed = {row[0] for row in conn.execute("SELECT post_key FROM dismissed_posts WHERE user_id=?", (uid,))}
    profile = profiles.head(conn, uid)
    terms = json.loads(profile["search_terms"]) if profile else []
    candidates = public_candidates(conn, account["bluesky_did"], terms, settings().candidate_pool_size)
    posts = []
    for post in candidates:
        if (
            post.key in dismissed
            or post.author_key in blocked(config)
            or screen.label_blocked(screen.labels_of(conn, post.key))
            or triage.hidden(triage.flags_of(conn, post))
        ):
            continue
        posts.append(post)
        if len(posts) >= delivery_limit(config):
            break
    return present(conn, uid, posts)
