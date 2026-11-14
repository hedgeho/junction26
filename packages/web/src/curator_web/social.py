"""Explicit user-initiated Bluesky interactions using the user's OAuth session."""

import re
from datetime import UTC, datetime

import httpx

from . import oauth

APPVIEW = "did:web:api.bsky.app#bsky_appview"


async def states(did: str, uris: list[str]) -> dict:
    session = await oauth.client().restore(did)
    base = session.pds_endpoint.rstrip("/") + "/xrpc/app.bsky.feed.getPosts"
    items = {}
    async with httpx.AsyncClient(auth=session.httpx_auth, timeout=20, follow_redirects=False) as client:
        for offset in range(0, len(uris), 25):
            response = await client.get(
                base, params=[("uris", uri) for uri in uris[offset : offset + 25]], headers={"atproto-proxy": APPVIEW}
            )
            response.raise_for_status()
            for post in response.json().get("posts", []):
                viewer = post.get("viewer") or {}
                items[post["uri"]] = {
                    "like": bool(viewer.get("like")),
                    "repost": bool(viewer.get("repost")),
                    "save": bool(viewer.get("bookmarked")),
                }
    return items


async def interact(did: str, uri: str, action: str, text: str = "") -> dict:
    session = await oauth.client().restore(did)
    base = session.pds_endpoint.rstrip("/") + "/xrpc/"
    async with httpx.AsyncClient(auth=session.httpx_auth, timeout=20, follow_redirects=False) as client:
        response = await client.get(
            base + "app.bsky.feed.getPosts", params={"uris": uri}, headers={"atproto-proxy": APPVIEW}
        )
        response.raise_for_status()
        posts = response.json().get("posts", [])
        post = next((p for p in posts if p.get("uri") == uri), None)
        if not post or not post.get("cid"):
            raise ValueError("Post unavailable")
        ref = {"uri": uri, "cid": post["cid"]}
        viewer = post.get("viewer") or {}
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        if action in ("like", "repost"):
            collection = "app.bsky.feed." + action
            existing = viewer.get(action)
            if existing:
                prefix = f"at://{did}/{collection}/"
                if not isinstance(existing, str) or not existing.startswith(prefix):
                    raise ValueError("Invalid record")
                payload = {"repo": did, "collection": collection, "rkey": existing[len(prefix) :]}
                method = "com.atproto.repo.deleteRecord"
                active = False
            else:
                payload = {
                    "repo": did,
                    "collection": collection,
                    "record": {"$type": collection, "subject": ref, "createdAt": now},
                }
                method = "com.atproto.repo.createRecord"
                active = True
        elif action == "save":
            active = not bool(viewer.get("bookmarked"))
            method = "app.bsky.bookmark.createBookmark" if active else "app.bsky.bookmark.deleteBookmark"
            payload = ref if active else {"uri": uri}
        elif action in ("comment", "quote"):
            if not text.strip() or len(text.encode("utf-8")) > 3000 or len(text) > 300:
                raise ValueError("Write a message of up to 300 characters")
            record = {"$type": "app.bsky.feed.post", "text": text, "createdAt": now}
            if action == "quote":
                record["embed"] = {"$type": "app.bsky.embed.record", "record": ref}
            else:
                parent = (post.get("record") or {}).get("reply") or {}
                root = parent.get("root") or ref
                record["reply"] = {"root": root, "parent": ref}
            payload = {"repo": did, "collection": "app.bsky.feed.post", "record": record}
            method = "com.atproto.repo.createRecord"
            active = True
        else:
            raise ValueError("Unknown action")
        headers = {"atproto-proxy": APPVIEW} if action == "save" else {}
        result = await client.post(base + method, json=payload, headers=headers)
        result.raise_for_status()
        output = {"active": active}
        if action in ("comment", "quote"):
            record_uri = result.json().get("uri")
            if isinstance(record_uri, str) and record_uri.startswith(f"at://{did}/app.bsky.feed.post/"):
                output["record_uri"] = record_uri
        return output


async def delete_comment(did: str, source_uri: str, record_uri: str) -> None:
    prefix = f"at://{did}/app.bsky.feed.post/"
    if not record_uri.startswith(prefix):
        raise ValueError("You can only delete your own comments")
    rkey = record_uri[len(prefix) :]
    if not re.fullmatch(r"[A-Za-z0-9._~:-]{1,512}", rkey) or rkey in (".", ".."):
        raise ValueError("Invalid comment")
    session = await oauth.client().restore(did)
    base = session.pds_endpoint.rstrip("/") + "/xrpc/"
    params = {"repo": did, "collection": "app.bsky.feed.post", "rkey": rkey}
    async with httpx.AsyncClient(auth=session.httpx_auth, timeout=20, follow_redirects=False) as client:
        response = await client.get(base + "com.atproto.repo.getRecord", params=params)
        response.raise_for_status()
        record = response.json()
        parent = ((record.get("value") or {}).get("reply") or {}).get("parent") or {}
        if record.get("uri") != record_uri or parent.get("uri") != source_uri or not record.get("cid"):
            raise ValueError("This comment does not belong to this post")
        result = await client.post(base + "com.atproto.repo.deleteRecord", json={**params, "swapRecord": record["cid"]})
        result.raise_for_status()
