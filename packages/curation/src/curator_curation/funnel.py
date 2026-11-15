"""Per-reader funnel: each candidate counted once, at the first stage that removed it. Counts only."""

import json
import time

from curator_core.sources import get_post

from . import digests, profiles, screen, triage, worker
from .candidates import CANDIDATE_WINDOW_H, public_candidates
from .config import settings

LABELS = {
    "firehose": "Bluesky firehose",
    "language": "Other languages",
    "window": "Stored from the last 24 hours",
    "not_recalled": "Not from your follows, their likes, For You, your topics or trends",
    "blocked": "Authors you blocked",
    "candidates": "Your candidates",
    "campaign": "Copy-paste campaigns",
    "author_rate": "Accounts posting too often",
    "tags": "Hashtag or mention stuffing",
    "labels": "Bluesky moderation labels",
    "pending": "Not scored yet",
    "scored": "Read by the model",
    "off_topic": "Not for you",
    "relevant": "Relevant to you",
    "clickbait": "Clickbait",
    "ragebait": "Ragebait",
    "spam_promo": "Spam or promotion",
    "unchecked": "Nearby interests not yet flag-checked",
    "downranked": "Nearby interests with a content flag",
    "clean": "Passed content checks",
    "seen": "Already shown or dismissed",
    "old": "Older than a day",
    "gone": "Edited or deleted",
    "conversation": "Same conversation",
    "duplicate": "Duplicate text",
    "story": "Same linked story",
    "author_cap": "More than two from one author",
    "limit": "Below today's picks",
    "delivered": "In today's feed",
    "misinformation": "Possible misinformation",
    "low_effort": "Low effort",
}
# Shown even at zero, so the content split stays visible.
ALWAYS = frozenset(triage.HIDE_AT)
CONTENT = frozenset({"labels", *triage.HIDE_AT, "unchecked", "downranked"})


def _firehose(conn, since: int) -> tuple[int, int, int] | None:
    """Daily posts and language drops, scaled from the complete hours counted in the window.

    The first counted hour and the current hour are partial, so they are left out.
    """
    first = conn.execute("SELECT min(hour) FROM firehose_stats").fetchone()[0]
    now_hour = int(time.time()) // 3600
    rows = conn.execute(
        "SELECT posts, lang_dropped FROM firehose_stats WHERE hour>=? AND hour>? AND hour<?",
        (since // 3600, first if first is not None else now_hour, now_hour),
    ).fetchall()
    if not rows:
        return None
    hours = len(rows)
    return round(sum(r[0] for r in rows) * 24 / hours), round(sum(r[1] for r in rows) * 24 / hours), hours


def _stored(conn, since: int) -> int:
    bluesky = conn.execute("SELECT count(*) FROM posts WHERE indexed_at>?", (since,)).fetchone()[0]
    other = conn.execute(
        "SELECT count(*) FROM social_posts WHERE platform<>'bluesky' AND deleted=0 AND observed_at>?", (since,)
    ).fetchone()[0]
    return bluesky + other


def _stage(key: str, count: int, drops: dict[str, int] | None = None) -> dict:
    items = [{"key": k, "label": LABELS[k], "count": n} for k, n in (drops or {}).items() if n or k in ALWAYS]
    return {"key": key, "label": LABELS[key], "count": count, "drops": items}


def _tally(keys: list[str], order) -> dict[str, int]:
    return {k: keys.count(k) for k in order}


def compute(conn, uid: str, did: str) -> dict:
    cfg = settings()
    config = digests.config_for(conn, uid)
    if not config:
        return {"stages": [], "notes": {}}
    since = int(time.time()) - CANDIDATE_WINDOW_H * 3600
    profile = profiles.head(conn, uid)
    terms = json.loads(profile["search_terms"]) if profile else []
    recalled = {p.key: p for p in public_candidates(conn, did, terms, cfg.candidate_pool_size)}
    last = digests.latest(conn, uid)
    edition = []
    if last and last["day"] == digests.day_key(config):
        edition = [
            r[0]
            for r in conn.execute("SELECT post_key FROM digest_items WHERE run_id=? ORDER BY position", (last["id"],))
        ]
    for key in edition:
        if key not in recalled and (post := get_post(conn, key)):
            recalled[key] = post
    blocked_authors = digests.blocked(config)
    pool = [p for p in recalled.values() if p.author_key not in blocked_authors]
    blocked_n = len(recalled) - len(pool)
    stored = max(_stored(conn, since), len(recalled))

    version = worker.scorer_version()
    revision = digests.revision(profile)
    hashes = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT post_key, content_hash FROM assessments_v2 WHERE user_id=? AND profile_revision=? AND scorer_version=?",
            (uid, revision, version),
        )
    }
    assessed = {p.key for p in pool if hashes.get(p.key) == p.content_hash}
    unassessed = [p for p in pool if p.key not in assessed]
    followed = {r[0] for r in conn.execute("SELECT subject_did FROM follows WHERE user_did=?", (did,))}
    cheap = screen.cheap_reasons(conn, unassessed, followed)
    early = [
        cheap.get(p.key) or ("labels" if screen.label_blocked(screen.labels_of(conn, p.key)) else "pending")
        for p in unassessed
    ]

    rows, nearby = digests.assessments(conn, uid, profile, version)
    rows = [r for r in rows if r["post_key"] in assessed]
    nearby = [r for r in nearby if r["post_key"] in assessed]
    relevant = len(rows) + len(nearby)
    reasons: dict[str, str] = {}
    skip = digests.seen(conn, uid, config)
    main = digests.ranked(conn, rows, config, skip, strict=False, reasons=reasons)
    serendipity = digests.ranked(conn, nearby, config, skip, strict=True, reasons=reasons)
    content = [r for r in reasons.values() if r in CONTENT]
    later = [r for r in reasons.values() if r not in CONTENT]

    eligible = list({p.key: p for _, p in [*main, *serendipity]}.values())
    shown = set(edition)
    selection = digests.Selection()
    for post in sorted((p for p in eligible if p.key in shown), key=lambda p: edition.index(p.key)):
        selection.add(post)
    later += [selection.conflict(p) or "limit" for p in eligible if p.key not in shown]

    flags = [triage.flags_of(conn, p) for _, p in main]
    downranked = {name: sum(name in triage.downranks(f) for f in flags) for name in triage.DOWNRANK_AT}

    clean = relevant - len(content)
    stages = []
    if firehose := _firehose(conn, since):
        stage = _stage("firehose", firehose[0], {"language": firehose[1]})
        if firehose[2] < 24:
            stage["label"] = f"Bluesky firehose per day, measured over {firehose[2]} h"
        stages.append(stage)
    stages += [
        _stage("window", stored, {"not_recalled": stored - len(recalled), "blocked": blocked_n}),
        _stage("candidates", len(pool), _tally(early, ["campaign", "author_rate", "tags", "labels", "pending"])),
        _stage("scored", len(assessed), {"off_topic": len(assessed) - relevant}),
        _stage("relevant", relevant, _tally(content, ["labels", *triage.HIDE_AT, "unchecked", "downranked"])),
        _stage(
            "clean",
            clean,
            _tally(
                later, ["seen", "old", "gone", "blocked", "conversation", "duplicate", "story", "author_cap", "limit"]
            ),
        ),
        _stage("delivered", len(selection.posts)),
    ]
    return {
        "stages": stages,
        "ranking": {
            "weights": digests.WEIGHTS,
            "half_life_h": digests.HALF_LIFE_H,
            "flag_factor": triage.DOWNRANK_FACTOR,
            "min_relevance": digests.relevance_floor(version),
            "max_per_author": digests.MAX_PER_AUTHOR,
            "daily_limit": digests.delivery_limit(config),
        },
        "notes": {
            "downranked": [{"key": k, "label": LABELS[k], "count": n} for k, n in downranked.items() if n],
            "flags_pending": sum(f is None for f in flags),
            "nearby": len(serendipity),
        },
    }
