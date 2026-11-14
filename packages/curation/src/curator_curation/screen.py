"""Free checks before any model call: platform labels, copy-paste campaigns, account post rate, tag spam."""

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from curator_core import db
from curator_core.sources import SocialPost

from .config import settings

log = logging.getLogger(__name__)

# Bluesky moderation and self-label values that remove a post from curation. Applies to post and author labels.
BLOCKING_LABELS = frozenset(
    {
        "!hide",
        "!takedown",
        "spam",
        "scam",
        "impersonation",
        "engagement-farming",
        "porn",
        "sexual",
        "nudity",
        "graphic-media",
        "gore",
    }
)
UNAVAILABLE = "!unavailable"
LABEL_TTL_S = 6 * 3600
WINDOW_S = 24 * 3600
_HASHTAG = re.compile(r"(?<![\w&])#\w+")
_MENTION = re.compile(r"(?<![\w])@[\w.-]+\.\w+")
_TRACKING = re.compile(r"^(utm_\w+|fbclid|gclid|ref|ref_src|s|si)$")


def _chunks(items: list, n: int):
    for i in range(0, len(items), n):
        yield items[i : i + n]


def fetch_labels(uris: list[str]) -> dict[str, list[str]]:
    """AppView post views carry Bluesky moderation, self and author labels. Missing posts are unavailable."""
    out = {}
    with httpx.Client(base_url=settings().public_appview, timeout=15) as client:
        response = client.get("/xrpc/app.bsky.feed.getPosts", params=[("uris", u) for u in uris])
        response.raise_for_status()
        for view in response.json().get("posts", []):
            labels = [lab["val"] for lab in view.get("labels") or [] if not lab.get("neg")]
            labels += [
                "author:" + lab["val"] for lab in (view.get("author") or {}).get("labels") or [] if not lab.get("neg")
            ]
            out[view.get("uri")] = sorted(set(labels))
    return {uri: out.get(uri, [UNAVAILABLE]) for uri in uris}


def hydrate_labels(conn, posts: list[SocialPost]) -> None:
    now = int(time.time())
    bluesky = [p.key for p in posts if p.platform == "bluesky"]
    fresh = set()
    for chunk in _chunks(bluesky, 500):
        fresh |= {
            r[0]
            for r in conn.execute(
                f"SELECT post_key FROM post_labels WHERE checked_at>? AND post_key IN ({','.join('?' * len(chunk))})",
                (now - LABEL_TTL_S, *chunk),
            )
        }
    uris = [key.removeprefix("bluesky:") for key in bluesky if key not in fresh]
    if not uris:
        return
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(fetch_labels, chunk) for chunk in _chunks(uris, 25)]
        results = {}
        for future in futures:
            try:
                results |= future.result()
            except Exception:  # noqa: BLE001 - labels are an optional signal; fail open
                log.warning("Label lookup failed; posts stay eligible until the next check")
    with db.tx(conn):
        conn.execute("DELETE FROM post_labels WHERE checked_at<?", (now - WINDOW_S,))
        conn.executemany(
            "INSERT OR REPLACE INTO post_labels VALUES (?,?,?)",
            [("bluesky:" + uri, json.dumps(labels), now) for uri, labels in results.items()],
        )


def labels_of(conn, key: str) -> list[str]:
    row = conn.execute("SELECT labels FROM post_labels WHERE post_key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else []


def label_blocked(labels: list[str]) -> bool:
    return any(label == UNAVAILABLE or label.removeprefix("author:") in BLOCKING_LABELS for label in labels)


def _author_counts(conn, authors: list[str], since: int) -> dict[str, int]:
    counts = {}
    for chunk in _chunks(authors, 500):
        counts |= dict(
            conn.execute(
                f"""SELECT author_did, count(*) FROM posts WHERE indexed_at>?
                AND author_did IN ({",".join("?" * len(chunk))}) GROUP BY author_did""",
                (since, *chunk),
            ).fetchall()
        )
    return counts


def _campaign_uris(conn, uris: list[str], since: int, min_authors: int) -> set[str]:
    sigs = {}
    for chunk in _chunks(uris, 500):
        sigs |= dict(
            conn.execute(
                f"SELECT uri, text_sig FROM posts WHERE text_sig IS NOT NULL AND uri IN ({','.join('?' * len(chunk))})",
                chunk,
            ).fetchall()
        )
    spread = {}
    distinct = sorted(set(sigs.values()))
    for chunk in _chunks(distinct, 500):
        spread |= dict(
            conn.execute(
                f"""SELECT text_sig, count(DISTINCT author_did) FROM posts WHERE indexed_at>?
                AND text_sig IN ({",".join("?" * len(chunk))}) GROUP BY text_sig""",
                (since, *chunk),
            ).fetchall()
        )
    return {uri for uri, sig in sigs.items() if spread.get(sig, 0) >= min_authors}


def screen(conn, posts: list[SocialPost], followed: set[str], limit: int) -> tuple[list[SocialPost], dict[str, int]]:
    """Returns up to `limit` eligible posts in input order, and drop counts by reason.

    Followed authors are exempt from the rate and tag checks, never from labels or copy-paste campaigns.
    """
    cfg = settings()
    since = int(time.time()) - WINDOW_S
    dropped = {"labels": 0, "campaign": 0, "author_rate": 0, "tags": 0}
    bluesky_authors = sorted({p.author_id for p in posts if p.platform == "bluesky" and p.author_id not in followed})
    rates = _author_counts(conn, bluesky_authors, since)
    campaigns = _campaign_uris(
        conn, [p.native_id for p in posts if p.platform == "bluesky"], since, cfg.campaign_min_authors
    )
    cheap = []
    for post in posts:
        if post.native_id in campaigns:
            dropped["campaign"] += 1
        elif post.author_id not in followed and rates.get(post.author_id, 0) > cfg.max_author_posts_per_day:
            dropped["author_rate"] += 1
        elif post.author_id not in followed and (
            len(_HASHTAG.findall(post.text)) >= cfg.max_tags or len(_MENTION.findall(post.text)) >= cfg.max_tags
        ):
            dropped["tags"] += 1
        else:
            cheap.append(post)
    # Labels need a network lookup, so check only what could be scored, with headroom for drops.
    head = cheap[: limit + limit // 4]
    if cfg.label_checks:
        hydrate_labels(conn, head)
    kept = []
    for post in head:
        if cfg.label_checks and label_blocked(labels_of(conn, post.key)):
            dropped["labels"] += 1
            continue
        kept.append(post)
        if len(kept) >= limit:
            break
    return kept, dropped


def story_key(post: SocialPost) -> str | None:
    """Normalized link URL, so the same story shared by many accounts is delivered once."""
    for item in post.media:
        if item.get("type") != "link" or not item.get("url"):
            continue
        parsed = urlparse(item["url"])
        host = (parsed.hostname or "").removeprefix("www.")
        if not host:
            continue
        query = urlencode([(k, v) for k, v in parse_qsl(parsed.query) if not _TRACKING.match(k)])
        return urlunparse(("", host, parsed.path.rstrip("/"), "", query, ""))
    return None
