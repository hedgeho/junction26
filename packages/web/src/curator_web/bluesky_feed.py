"""Reader-authenticated For You snapshots. Tokens never leave the OAuth/PDS path."""

import time
from urllib.parse import quote

import httpx
from curator_core import db, media
from curator_core.imports import import_posts

from . import oauth

FEED_URI = "at://did:plc:3guzzweuqraryl3rdkimjamk/app.bsky.feed.generator/for-you"
FEED_URL = "https://bsky.app/profile/spacecowboy17.bsky.social/feed/for-you"
APPVIEW_SERVICE = "did:web:api.bsky.app#bsky_appview"
REFRESH_SECONDS = 15 * 60
AUTH_NOTICE_URI = "at://did:plc:qhyg27lbj3uqsmlyvmsh2bjx/app.bsky.feed.post/3mnqknfvlpk24"


class AuthenticationRequired(Exception):
    pass


def normalize_feed(payload: dict) -> list[dict]:
    """Preserve the full original text; do not import feed authentication notices."""
    items = payload.get("feed")
    if not isinstance(items, list) or len(items) > 100:
        raise ValueError("Invalid feed response")
    records = {}
    for item in items:
        post = item.get("post", {})
        record, author = post.get("record", {}), post.get("author", {})
        uri, did, text = post.get("uri", ""), author.get("did", ""), record.get("text")
        if uri == AUTH_NOTICE_URI or (
            author.get("handle") == "foryou.club"
            and isinstance(text, str)
            and text.startswith("This feed requires authentication.")
        ):
            raise AuthenticationRequired()
        if not did.startswith("did:") or not uri.startswith(f"at://{did}/app.bsky.feed.post/"):
            raise ValueError("Invalid post identity")
        if not isinstance(text, str):
            raise TypeError("Missing original text")
        # Optional union fields can be explicitly null in hydrated feed records.
        # Null metadata must not discard the original post or fail the whole page.
        reply = record.get("reply") or {}
        embed = record.get("embed") or {}
        quoted = embed.get("record") or {}
        if "record" in quoted:
            quoted = quoted["record"] or {}
        records.setdefault(
            uri,
            {
                "platform": "bluesky",
                "native_id": uri,
                "author_id": did,
                "author_name": author.get("handle") or did,
                "author_handle": author.get("handle"),
                "author_avatar": author.get("avatar"),
                "text": text,
                "canonical_url": f"https://bsky.app/profile/{quote(did, safe=':')}/post/{quote(uri.rsplit('/', 1)[-1], safe='')}",
                "created_at": record.get("createdAt"),
                "parent_id": (reply.get("parent") or {}).get("uri"),
                "root_id": (reply.get("root") or {}).get("uri"),
                "quote_id": quoted.get("uri"),
                "media": media.bluesky_media(did, record),
            },
        )
    return list(records.values())


async def fetch_feed(did: str) -> dict:
    session = await oauth.client().restore(did)
    async with httpx.AsyncClient(auth=session.httpx_auth, timeout=35, follow_redirects=False) as client:
        response = await client.get(
            session.pds_endpoint.rstrip("/") + "/xrpc/app.bsky.feed.getFeed",
            headers={"atproto-proxy": APPVIEW_SERVICE},
            params={"feed": FEED_URI, "limit": 100},
        )
        if response.status_code in (401, 403):
            raise AuthenticationRequired()
        response.raise_for_status()
        return response.json()


def status(conn, did: str) -> dict | None:
    row = conn.execute("SELECT * FROM bluesky_feed_sync WHERE user_did=?", (did,)).fetchone()
    return dict(row) if row else None


def save_snapshot(conn, did: str, records: list[dict]) -> None:
    import_posts(conn, records)
    now = int(time.time())
    with db.tx(conn):
        conn.execute("DELETE FROM bluesky_feed_posts WHERE user_did=?", (did,))
        conn.executemany(
            "INSERT INTO bluesky_feed_posts VALUES (?,?,?,?)",
            [(did, "bluesky:" + r["native_id"], i, now) for i, r in enumerate(records)],
        )
        conn.execute(
            "UPDATE bluesky_feed_sync SET state='ready',updated_at=?,post_count=? WHERE user_did=?",
            (now, len(records), did),
        )
        conn.execute(
            """UPDATE reader_settings SET refresh_requested=1,version=version+1
            WHERE user_id IN (SELECT id FROM reader_accounts WHERE bluesky_did=?)""",
            (did,),
        )


async def sync(did: str, *, force: bool = False, initialize: bool = False) -> None:
    """One bounded page on login/open/refresh, with a cross-request cooldown.

    Failures preserve the prior snapshot and never import an unauthenticated fallback.
    No provider exception or OAuth credential is included in user-visible state.
    """
    now = int(time.time())
    conn = db.connect()
    try:
        with db.tx(conn):
            previous = status(conn, did)
            if not previous and not initialize:
                return
            if previous and not initialize and now - previous["last_attempt"] < (30 if force else REFRESH_SECONDS):
                return
            if previous and previous["state"] == "auth_required" and not initialize:
                return
            conn.execute(
                """INSERT INTO bluesky_feed_sync(user_did,feed_uri,state,last_attempt)
                VALUES (?,?,'loading',?) ON CONFLICT(user_did) DO UPDATE SET state='loading',last_attempt=?""",
                (did, FEED_URI, now, now),
            )
        try:
            payload = await fetch_feed(did)
            save_snapshot(conn, did, normalize_feed(payload))
        except AuthenticationRequired:
            conn.execute("UPDATE bluesky_feed_sync SET state='auth_required' WHERE user_did=?", (did,))
        except Exception:  # noqa: BLE001 - keep provider/OAuth error payloads out of UI and logs
            conn.execute("UPDATE bluesky_feed_sync SET state='failed' WHERE user_did=?", (did,))
    finally:
        conn.close()
