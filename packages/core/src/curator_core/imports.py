"""Validated normalized public-source writes shared by source adapters."""

import time
from urllib.parse import urlparse

from curator_core import db, media

SOURCE_HOSTS = {
    "bluesky": ("bsky.app",),
    "x": ("x.com", "twitter.com"),
    "threads": ("threads.com", "www.threads.com", "threads.net", "www.threads.net"),
}


def import_posts(conn, records: list[dict]) -> int:
    if not isinstance(records, list) or len(records) > 2000:
        raise ValueError("expected at most 2000 public source records")
    rows = []
    for item in records:
        platform, native_id = item["platform"], item["native_id"]
        if platform not in SOURCE_HOSTS or not isinstance(native_id, str) or not 1 <= len(native_id) <= 1000:
            raise ValueError("invalid source identity")
        url = item.get("canonical_url", "")
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in SOURCE_HOSTS[platform]
            or parsed.username
            or parsed.password
        ):
            raise ValueError("invalid canonical URL")
        text, context = item.get("text", ""), item.get("context", "")
        if not isinstance(text, str) or not isinstance(context, str) or len(text) > 30000 or len(context) > 16000:
            raise ValueError("invalid source text")
        observed = int(item.get("observed_at", time.time()))
        if observed > time.time() + 60:
            raise ValueError("invalid observed timestamp")
        rows.append(
            (
                platform,
                native_id,
                item["author_id"],
                item.get("author_name", item["author_id"]),
                text,
                url,
                item.get("created_at"),
                observed,
                item.get("parent_id"),
                item.get("root_id"),
                item.get("quote_id"),
                context,
                int(bool(item.get("deleted", False))),
                media.dumps(media.clean(platform, item.get("media"))),
                handle[:253] if isinstance(handle := item.get("author_handle"), str) and handle else None,
                media.media_url(platform, item.get("author_avatar")),
            )
        )
    with db.tx(conn):
        conn.executemany(
            """INSERT INTO social_posts
            (platform,native_id,author_id,author_name,text,canonical_url,created_at,observed_at,
             parent_id,root_id,quote_id,context,deleted,media,author_handle,author_avatar)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(platform,native_id) DO UPDATE SET
            author_id=excluded.author_id,author_name=excluded.author_name,text=excluded.text,
            canonical_url=excluded.canonical_url,created_at=excluded.created_at,observed_at=excluded.observed_at,
            parent_id=excluded.parent_id,root_id=excluded.root_id,quote_id=excluded.quote_id,
            context=excluded.context,deleted=excluded.deleted,media=excluded.media,
            author_handle=excluded.author_handle,author_avatar=excluded.author_avatar""",
            rows,
        )
    return len(rows)
