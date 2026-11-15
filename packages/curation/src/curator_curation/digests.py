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
WEIGHTS = {"relevance": 0.65, "substance": 0.25, "opportunity": 0.1}
HALF_LIFE_H = 48
MAX_PER_AUTHOR = 2


def revision(profile):
    return profile["id"] if profile else BASELINE_REVISION


def day_key(config) -> str:
    return datetime.now(ZoneInfo(config["timezone"])).date().isoformat()


def delivery_limit(config) -> int:
    return config["daily_limit"]


def config_for(conn, uid):
    return conn.execute("SELECT * FROM reader_settings WHERE user_id=?", (uid,)).fetchone()


def latest(conn, uid):
    return conn.execute(
        "SELECT * FROM digest_runs WHERE user_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (uid,)
    ).fetchone()


def blocked(config) -> set[str]:
    return set(json.loads(config["blocked_authors"]))


def relevance_floor(scorer_version: str) -> float:
    if scorer_version.startswith(DECISION_SCORER_VERSION):
        return settings().decision_minimum_relevance
    return settings().minimum_relevance


def assessments(conn, uid, profile, scorer_version: str):
    """Digest-eligible assessments: (requested, nearby). Nearby is substantive but outside the profile."""
    decisions = scorer_version.startswith(DECISION_SCORER_VERSION)
    threshold = relevance_floor(scorer_version)
    rows = conn.execute(
        """SELECT * FROM assessments_v2 WHERE user_id=? AND profile_revision=? AND scorer_version=?
        AND decision='include' AND relevance>=?""",
        (uid, revision(profile), scorer_version, threshold),
    ).fetchall()
    nearby = (
        conn.execute(
            """SELECT * FROM assessments_v2 WHERE user_id=? AND profile_revision=? AND scorer_version=?
            AND decision='exclude' AND relevance>=0.5 AND substance>=0.75""",
            (uid, revision(profile), scorer_version),
        ).fetchall()
        if decisions
        else []
    )
    return rows, nearby


def seen(conn, uid, config) -> set[str]:
    """Dismissed posts and posts delivered on earlier days."""
    dismissed = {r[0] for r in conn.execute("SELECT post_key FROM dismissed_posts WHERE user_id=?", (uid,))}
    if not settings().hide_shown_posts:
        return dismissed
    previous = {
        r[0]
        for r in conn.execute(
            """SELECT i.post_key FROM digest_items i JOIN digest_runs d ON d.id=i.run_id
        WHERE d.user_id=? AND d.day<>?""",
            (uid, day_key(config)),
        )
    }
    return dismissed | previous


def followed_keys(conn, uid) -> set[str]:
    account = conn.execute("SELECT bluesky_did FROM reader_accounts WHERE id=?", (uid,)).fetchone()
    return {
        "bluesky:" + r[0]
        for r in conn.execute(
            "SELECT subject_did FROM follows WHERE user_did=?", (account["bluesky_did"] if account else "",)
        )
    }


def ranked(conn, rows, config, skip: set[str], strict: bool, reasons: dict[str, str] | None = None):
    """Eligible (value, post) pairs, best first. Records why each other row was dropped in `reasons`.

    Strict selection also requires completed content flags without any downranking flag.
    """
    reasons = {} if reasons is None else reasons
    out = []
    for row in rows:
        post = get_post(conn, row["post_key"])
        if not post or post.content_hash != row["content_hash"]:
            reasons[row["post_key"]] = "gone"
            continue
        if post.author_key in blocked(config):
            reasons[post.key] = "blocked"
            continue
        if screen.label_blocked(screen.labels_of(conn, post.key)):
            reasons[post.key] = "labels"
            continue
        flags = triage.flags_of(conn, post)
        if hide := triage.hide_reason(flags):
            reasons[post.key] = hide
            continue
        if strict and flags is None:
            reasons[post.key] = "unchecked"
            continue
        if strict and triage.penalty(flags) < 1:
            reasons[post.key] = "downranked"
            continue
        if post.key in skip:
            reasons[post.key] = "seen"
            continue
        if post.observed_at < time.time() - 86400:
            reasons[post.key] = "old"
            continue
        age_h = max(0, (time.time() - post.published_at) / 3600)
        value = sum(w * row[name] for name, w in WEIGHTS.items()) * 0.5 ** (age_h / HALF_LIFE_H)
        out.append((value * triage.penalty(flags), post))
    out.sort(key=lambda p: (-p[0], p[1].key))
    return out


class Selection:
    """One post per conversation, text and linked story, and at most two per author."""

    def __init__(self):
        self.posts, self.authors, self.conversations, self.texts, self.stories = [], {}, set(), set(), set()

    @staticmethod
    def _identity(post):
        conversation = (post.platform, post.root_id or post.parent_id or post.native_id)
        return conversation, " ".join(post.text.casefold().split()), screen.story_key(post)

    def conflict(self, post) -> str | None:
        conversation, normalized, story = self._identity(post)
        if conversation in self.conversations:
            return "conversation"
        if normalized in self.texts:
            return "duplicate"
        if story and story in self.stories:
            return "story"
        if self.authors.get(post.author_key, 0) >= MAX_PER_AUTHOR:
            return "author_cap"
        return None

    def add(self, post) -> None:
        conversation, normalized, story = self._identity(post)
        self.posts.append(post)
        self.conversations.add(conversation)
        self.texts.add(normalized)
        if story:
            self.stories.add(story)
        self.authors[post.author_key] = self.authors.get(post.author_key, 0) + 1

    def take(self, post) -> bool:
        if post in self.posts or self.conflict(post):
            return False
        self.add(post)
        return True


def build(conn, uid, scorer_version: str, selector=None, request=None) -> str | None:
    profile = profiles.head(conn, uid)
    config = config_for(conn, uid)
    if not config:
        return None
    rows, nearby = assessments(conn, uid, profile, scorer_version)
    skip = seen(conn, uid, config)
    if request:
        visible = json.loads(request["feed_context"])
        skip |= {
            item["post_key"] for item in visible if isinstance(item, dict) and isinstance(item.get("post_key"), str)
        }
    followed = followed_keys(conn, uid)
    pool = ranked(conn, rows, config, skip, strict=False)
    limit = delivery_limit(config)
    candidate_limit = min(100, max(limit, limit * 3)) if selector and hasattr(selector, "select_feed") else limit
    selection = Selection()
    selected, take = selection.posts, selection.take
    social = 0
    for _, post in pool:
        if social >= min(settings().social_slots, candidate_limit):
            break
        if post.author_key in followed and take(post):
            social += 1
    serendipity = [] if request else ranked(conn, nearby, config, skip, strict=True)
    reserved = min(len(serendipity), int(candidate_limit * settings().serendipity_share))
    for _, post in pool:
        if len(selected) >= candidate_limit - reserved:
            break
        take(post)
    for _, post in serendipity:
        if len(selected) >= candidate_limit:
            break
        take(post)
    rank = {post.key: i for i, (_, post) in enumerate(pool)}
    selected.sort(key=lambda p: rank.get(p.key, len(rank)))
    if selector and hasattr(selector, "select_feed") and selected:
        candidates = [
            {"post_key": p.key, "author": p.author_name, "text": p.text, "context": p.context} for p in selected
        ]
        instruction = (
            {"instruction": request["instruction"], "visible_posts": request["feed_context"]} if request else None
        )
        keys = selector.select_feed(candidates, limit, profiles.markdown(conn, uid) if profile else "", instruction)
        allowed = {p.key: p for p in selected}
        if len(keys) != len(set(keys)) or not set(keys) <= set(allowed):
            raise ValueError("invalid feed selection")
        selected = [allowed[key] for key in keys[:limit]]
    else:
        selected = selected[:limit]
    run_id = uuid.uuid4().hex
    with db.tx(conn):
        current = profiles.head(conn, uid)
        current_config = config_for(conn, uid)
        if revision(current) != revision(profile) or current_config["version"] != config["version"]:
            return None
        active = conn.execute("SELECT job_id FROM feed_requests WHERE user_id=?", (uid,)).fetchone()
        if (active["job_id"] if active else None) != (request["job_id"] if request else None):
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
        if request:
            conn.execute("DELETE FROM feed_requests WHERE user_id=? AND job_id=?", (uid, request["job_id"]))
            conn.execute(
                "UPDATE feedback_jobs SET feedback='', feed_context=NULL WHERE user_id=? AND mode='more' AND state='applied'",
                (uid,),
            )
    return run_id


def match_notes(conn, uid, run_id, gemma) -> int:
    posts = [
        get_post(conn, item["post_key"])
        for item in conn.execute(
            "SELECT post_key FROM digest_items WHERE run_id=? ORDER BY position", (run_id,)
        ).fetchall()
    ]
    return notes.match_feed(conn, uid, run_id, [post for post in posts if post], gemma)


def read(conn, uid) -> list[dict]:
    run = latest(conn, uid)
    config = config_for(conn, uid)
    if not config:
        return []
    # A rebuild in progress keeps the last selection on screen; unscored candidates only fill a reader's first wait.
    if not run:
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
