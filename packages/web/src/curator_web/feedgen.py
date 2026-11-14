"""Feed generator XRPC endpoints. getFeedSkeleton only reads feed_items."""

import asyncio
import time

from curator_core import db
from curator_curation.rank import GLOBAL
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
        c = db.connect()
        owner = GLOBAL
        if did:
            ensure_user(c, did)  # touch last_seen_at so curation and follow sync keep running
            if c.execute("SELECT 1 FROM feed_items WHERE user_did = ? LIMIT 1", (did,)).fetchone():
                owner = did
        return c.execute(
            "SELECT post_uri, reason FROM feed_items WHERE user_did = ? AND position >= ? ORDER BY position LIMIT ?",
            (owner, offset, limit),
        ).fetchall()

    rows = await asyncio.to_thread(read)
    body = {"feed": [{"post": r["post_uri"], "feedContext": (r["reason"] or "")[:2000]} for r in rows]}
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
    rows = [(did, i.get("item"), i.get("event", ""), now) for i in payload.get("interactions", [])]

    def write():
        c = db.connect()
        with db.tx(c):
            c.executemany("INSERT INTO interactions VALUES (?, ?, ?, ?)", rows)

    await asyncio.to_thread(write)
    return {}
