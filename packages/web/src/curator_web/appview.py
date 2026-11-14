"""Unauthenticated reads from the public Bluesky AppView, for rendering the site."""

import httpx

from .config import settings


def client() -> httpx.Client:
    return httpx.Client(base_url=settings().public_appview, timeout=20)


def resolve_handle(handle: str) -> str:
    with client() as c:
        r = c.get("/xrpc/com.atproto.identity.resolveHandle", params={"handle": handle.lstrip("@")})
        r.raise_for_status()
        return r.json()["did"]


def get_profile(actor: str) -> dict:
    with client() as c:
        r = c.get("/xrpc/app.bsky.actor.getProfile", params={"actor": actor})
        r.raise_for_status()
        return r.json()


def get_posts(uris: list[str]) -> list[dict]:
    """Hydrated post views (author, counts, embeds)."""
    out = []
    with client() as c:
        for i in range(0, len(uris), 25):
            r = c.get("/xrpc/app.bsky.feed.getPosts", params=[("uris", u) for u in uris[i : i + 25]])
            r.raise_for_status()
            out += r.json().get("posts", [])
    return out


def get_thread(uri: str) -> dict:
    """Fetch direct replies and ancestors for an on-demand thread branch."""
    with client() as c:
        response = c.get("/xrpc/app.bsky.feed.getPostThread", params={"uri": uri, "depth": 1, "parentHeight": 1000})
        response.raise_for_status()
        return response.json().get("thread", {})
