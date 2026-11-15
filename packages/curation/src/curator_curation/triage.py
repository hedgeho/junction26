"""Reader-independent content flags, assessed once per post version and shared by every reader."""

import logging
import time
from concurrent.futures import ThreadPoolExecutor

from curator_core import db
from curator_core.sources import SocialPost

from .llm.gemini import FLAG_CRITERIA, TRIAGE_VERSION, PublicCandidate

log = logging.getLogger(__name__)

FLAGS = tuple(FLAG_CRITERIA)
# Calibrated for Gemini Decision 1 against reference labels: hide at precision >= 0.8.
HIDE_AT = {"clickbait": 0.78, "ragebait": 0.88, "spam_promo": 0.36}
# Never hidden on a model's word alone; each flag halves the digest value.
DOWNRANK_AT = {"misinformation": 0.84, "ragebait": 0.76, "low_effort": 0.82}
RETENTION_S = 48 * 3600


def version(model: str) -> str:
    return f"{TRIAGE_VERSION}:{model}"


def candidate(post: SocialPost, media: tuple[dict, ...] = ()) -> PublicCandidate:
    return PublicCandidate(
        post.key, post.platform, post.text, post.context, bool(post.parent_id), bool(post.quote_id), media
    )


def missing(conn, posts: list[SocialPost], model: str) -> list[SocialPost]:
    v = version(model)
    return [
        p
        for p in posts
        if not conn.execute(
            "SELECT 1 FROM post_flags WHERE post_key=? AND content_hash=? AND version=?", (p.key, p.content_hash, v)
        ).fetchone()
    ]


def assess(triager, candidates: list[PublicCandidate], concurrency: int) -> dict[str, dict[str, float]]:
    """Provider calls only; failures leave a post unflagged so it stays eligible."""

    def one(c):
        try:
            return c.post_key, triager.flags(c)
        except Exception:  # noqa: BLE001 - isolate provider failures
            return c.post_key, None

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        results = dict(pool.map(one, candidates))
    failed = sum(v is None for v in results.values())
    if failed:
        log.warning("Triage failed for %d posts; retry next cycle", failed)
    return {k: v for k, v in results.items() if v is not None}


def store(conn, posts: list[SocialPost], flags: dict[str, dict[str, float]], model: str) -> None:
    now = int(time.time())
    by_key = {p.key: p for p in posts}
    with db.tx(conn):
        conn.execute("DELETE FROM post_flags WHERE assessed_at<?", (now - RETENTION_S,))
        conn.executemany(
            f"INSERT OR REPLACE INTO post_flags VALUES (?,?,?,{','.join('?' * len(FLAGS))},?)",
            [
                (key, by_key[key].content_hash, version(model), *(f[name] for name in FLAGS), now)
                for key, f in flags.items()
                if key in by_key
            ],
        )


def flags_of(conn, post: SocialPost) -> dict[str, float] | None:
    row = conn.execute(
        "SELECT * FROM post_flags WHERE post_key=? AND content_hash=? ORDER BY assessed_at DESC LIMIT 1",
        (post.key, post.content_hash),
    ).fetchone()
    return {name: row[name] for name in FLAGS} if row else None


def hide_reason(flags: dict[str, float] | None) -> str | None:
    """The hiding flag that clears its threshold by the widest margin, if any."""
    over = {name: flags[name] - threshold for name, threshold in HIDE_AT.items() if flags and flags[name] >= threshold}
    return max(over, key=over.get) if over else None


def hidden(flags: dict[str, float] | None) -> bool:
    return hide_reason(flags) is not None


def downranks(flags: dict[str, float] | None) -> list[str]:
    return [name for name, threshold in DOWNRANK_AT.items() if flags and flags[name] >= threshold]


def penalty(flags: dict[str, float] | None) -> float:
    return 0.5 ** len(downranks(flags))
