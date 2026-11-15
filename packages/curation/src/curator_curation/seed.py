"""First profile for a reader with none: topics evident in their own recent public posts."""

import logging
import time

import httpx
from curator_core import db

from . import profiles
from .config import settings

log = logging.getLogger(__name__)

MIN_POSTS = 5
RETRY_S = 24 * 3600


def own_posts(did: str, limit: int = 50) -> list[str]:
    """Original top-level posts by the reader, from the public AppView; reposts and replies excluded."""
    with httpx.Client(base_url=settings().public_appview, timeout=15) as client:
        response = client.get(
            "/xrpc/app.bsky.feed.getAuthorFeed", params={"actor": did, "limit": limit, "filter": "posts_no_replies"}
        )
        response.raise_for_status()
    texts = []
    for item in response.json().get("feed", []):
        post = item.get("post") or {}
        text = (post.get("record") or {}).get("text") or ""
        if not item.get("reason") and (post.get("author") or {}).get("did") == did and len(text.strip()) >= 20:
            texts.append(text)
    return texts


def body(interests: list[str]) -> str:
    lines = "\n".join(f"- [topic] More: {i}" for i in interests)
    return (
        "# Social filtering profile\n\n## Goals\n"
        "Seeded from the reader's recent public posts; feedback and edits refine it.\n\n"
        f"Topics the reader posts about:\n{lines}\n"
    )


def maybe_seed(conn, did: str, uid: str, seeder, fetch=own_posts) -> bool:
    """At most one attempt a day, and only while the reader has no profile at all."""
    key = "profile-seed:" + uid
    if profiles.head(conn, uid) or time.time() - int(db.kv_get(conn, key) or "0") < RETRY_S:
        return False
    db.kv_set(conn, key, str(int(time.time())))
    try:
        texts = fetch(did)
        if len(texts) < MIN_POSTS:
            return False
        seed = seeder.seed(texts)
    except Exception:  # noqa: BLE001 - provider errors stay out of logs; the neutral baseline remains
        log.warning("Profile seeding failed for a reader; retry tomorrow")
        return False
    interests = [i.strip() for i in seed.interests if i.strip()]
    if not interests:
        return False
    try:
        profiles.save(conn, uid, "public", body(interests), None, terms=seed.search_terms, origin="seed-from-posts")
    except profiles.RevisionConflict:
        return False
    return True
