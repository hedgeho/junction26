"""Platform-neutral read contract. Source adapters write through the shared validated importer."""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from urllib.parse import quote

from . import media as media_refs

Platform = Literal["bluesky", "x", "threads"]


@dataclass(frozen=True)
class SocialPost:
    key: str
    platform: Platform
    native_id: str
    author_id: str
    author_name: str
    text: str
    canonical_url: str
    created_at: str | None
    observed_at: int
    parent_id: str | None = None
    root_id: str | None = None
    quote_id: str | None = None
    context: str = ""
    author_handle: str | None = None
    author_avatar: str | None = None
    media: tuple[dict, ...] = field(default=(), hash=False)
    facets: str | None = None

    @property
    def content_hash(self) -> str:
        values = [self.text, self.author_id, self.created_at, self.parent_id, self.root_id, self.quote_id, self.context]
        # Appended only when present so text-only posts keep their existing hashes.
        if self.media:
            values.append(list(self.media))
        return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()

    @property
    def author_key(self) -> str:
        return f"{self.platform}:{self.author_id}"

    @property
    def published_at(self) -> float:
        try:
            value = datetime.fromisoformat(self.created_at) if self.created_at else None
            if value and value.tzinfo:
                return min(value.timestamp(), self.observed_at)
        except (ValueError, OverflowError):
            pass
        return float(self.observed_at)


def _bluesky(row) -> SocialPost:
    uri = row["uri"]
    rkey = uri.rsplit("/", 1)[-1]
    return SocialPost(
        key=f"bluesky:{uri}",
        platform="bluesky",
        native_id=uri,
        author_id=row["author_did"],
        author_name=row["author_did"],
        text=row["text"],
        canonical_url=f"https://bsky.app/profile/{quote(row['author_did'], safe=':')}/post/{quote(rkey, safe='')}",
        created_at=row["created_at"],
        observed_at=row["indexed_at"],
        parent_id=row["reply_parent"],
        root_id=row["reply_root"],
        quote_id=row["embed_ref"] if row["embed_kind"] == "record" else None,
        author_handle=_optional(row, "author_handle"),
        author_avatar=_optional(row, "author_avatar"),
        media=tuple(media_refs.loads(row["media"])),
        facets=_optional(row, "facets"),
    )


def _optional(row, field):
    return row[field] if field in row.keys() else None  # noqa: SIM118 - sqlite Row membership checks values


def _social(row) -> SocialPost:
    return SocialPost(
        key=f"{row['platform']}:{row['native_id']}",
        platform=row["platform"],
        native_id=row["native_id"],
        author_id=row["author_id"],
        author_name=row["author_name"],
        text=row["text"],
        canonical_url=row["canonical_url"],
        created_at=row["created_at"],
        observed_at=row["observed_at"],
        parent_id=row["parent_id"],
        root_id=row["root_id"],
        quote_id=row["quote_id"],
        context=row["context"],
        author_handle=_optional(row, "author_handle"),
        author_avatar=_optional(row, "author_avatar"),
        media=tuple(media_refs.loads(row["media"])),
        facets=_optional(row, "facets"),
    )


def get_post(conn, key: str) -> SocialPost | None:
    platform, sep, native_id = key.partition(":")
    if not sep or platform not in ("bluesky", "x", "threads"):
        return None
    row = conn.execute("SELECT * FROM social_posts WHERE platform=? AND native_id=?", (platform, native_id)).fetchone()
    if row is not None:
        return None if row["deleted"] else _social(row)
    if platform == "bluesky":
        row = conn.execute("SELECT * FROM posts WHERE uri=?", (native_id,)).fetchone()
        return _bluesky(row) if row else None
    return None


def recent_posts(conn, since: int, limit: int = 2000) -> list[SocialPost]:
    # A normalized source row (including a tombstone) overrides a legacy Bluesky row.
    normalized = conn.execute(
        "SELECT * FROM social_posts WHERE observed_at>? ORDER BY observed_at DESC LIMIT ?", (since, limit)
    )
    posts = {f"{r['platform']}:{r['native_id']}": _social(r) for r in normalized if not r["deleted"]}
    legacy = conn.execute(
        """SELECT p.* FROM posts p WHERE indexed_at>?
        AND NOT EXISTS (SELECT 1 FROM social_posts s WHERE s.platform='bluesky' AND s.native_id=p.uri)
        ORDER BY indexed_at DESC LIMIT ?""",
        (since, limit),
    )
    for row in legacy:
        post = _bluesky(row)
        posts.setdefault(post.key, post)
    return sorted(posts.values(), key=lambda p: p.observed_at, reverse=True)[:limit]
