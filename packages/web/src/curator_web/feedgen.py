"""Feed generator XRPC endpoints. getFeedSkeleton only reads feed_items."""

import asyncio
import time

from curator_core import db
from curator_curation import digests, profiles
from curator_curation.readers import ensure_user
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from . import feedauth
from .config import settings

router = APIRouter()


@router.get("/.well-known/did.json")
def did_document():
    cfg = settings()
    return {
        "@context": ["https://www.w3.org/ns/did/v1"],
        "id": cfg.service_did,
        "service": [{"id": "#bsky_fg", "type": "BskyFeedGenerator", "serviceEndpoint": f"https://{cfg.hostname}"}],
    }


@router.get("/xrpc/app.bsky.feed.describeFeedGenerator")
def describe_feed_generator():
    cfg = settings()
    return {
        "did": cfg.service_did,
        "feeds": [{"uri": cfg.feed_uri}],
        "links": {"privacyPolicy": f"https://{cfg.hostname}/privacy"},
    }


@router.get("/xrpc/app.bsky.feed.getFeedSkeleton")
async def get_feed_skeleton(
    feed: str, limit: int = 50, cursor: str | None = None, authorization: str | None = Header(None)
):
    if feed != settings().feed_uri:
        return JSONResponse({"error": "UnknownFeed", "message": "unknown feed"}, status_code=400)
    did = await feedauth.requester_did(authorization, "app.bsky.feed.getFeedSkeleton")
    limit = max(1, min(limit, 100))
    offset = int(cursor) if cursor and cursor.isdigit() else 0

    def read():
        if not did:
            return []
        c = db.connect()
        try:
            ensure_user(c, did)
            uid = profiles.bootstrap(c, did)
            # This adapter only emits Bluesky references, never generated feedContext.
            items = [p for p in digests.read(c, uid) if p["platform"] == "bluesky"]
            return items[offset : offset + limit]
        finally:
            c.close()

    rows = await asyncio.to_thread(read)
    body = {"feed": [{"post": r["key"].removeprefix("bluesky:"), "feedContext": ""} for r in rows]}
    if len(rows) == limit:
        body["cursor"] = str(offset + limit)
    return body


@router.post("/xrpc/app.bsky.feed.sendInteractions")
async def send_interactions(request: Request, authorization: str | None = Header(None)):
    did = await feedauth.requester_did(authorization, "app.bsky.feed.sendInteractions")
    if not did:
        raise HTTPException(401)
    payload = await request.json()
    now = int(time.time())
    interactions = payload.get("interactions", [])
    if not isinstance(interactions, list) or len(interactions) > 100:
        raise HTTPException(400)
    rows = [(did, i.get("item"), i.get("event", ""), now) for i in interactions if isinstance(i, dict)]

    def write():
        c = db.connect()
        try:
            uid = profiles.reader_id(c, did)
            with db.tx(c):
                c.executemany("INSERT INTO interactions VALUES (?, ?, ?, ?)", rows)
                for _, uri, event, _ in rows:
                    if isinstance(uri, str) and isinstance(event, str) and event.endswith("requestLess"):
                        c.execute("INSERT OR REPLACE INTO dismissed_posts VALUES (?,?,?)", (uid, "bluesky:" + uri, now))
        finally:
            c.close()

    await asyncio.to_thread(write)
    return {}
