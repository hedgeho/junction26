"""Post media and author avatars as validated, platform-neutral references.

Only references are stored: image bytes stay on the platform CDN. Every stored
URL is HTTPS on that platform's media hosts, so the reader can render it and
curation can fetch it without reaching arbitrary hosts.
"""

import json
import re
from urllib.parse import urlparse

MEDIA_HOSTS = {
    "bluesky": ("cdn.bsky.app", "video.cdn.bsky.app"),
    "x": ("pbs.twimg.com",),
}
# Threads serves signed media from rotating Meta CDN hosts, so match on the dot boundary.
MEDIA_HOST_SUFFIXES = {"threads": (".cdninstagram.com", ".fbcdn.net")}
MAX_IMAGES = 4
_CID = re.compile(r"^[a-z2-7]{20,120}$")
_DID = re.compile(r"^did:[a-z]+:[A-Za-z0-9._:%-]{1,200}$")


def _media_host_allowed(platform: str, host: str | None) -> bool:
    if not host:
        return False
    return host in MEDIA_HOSTS.get(platform, ()) or host.endswith(MEDIA_HOST_SUFFIXES.get(platform, ()))


def media_url(platform: str, url) -> str | None:
    if not isinstance(url, str) or len(url) > 2000:
        return None
    parsed = urlparse(url)
    if parsed.scheme != "https" or not _media_host_allowed(platform, parsed.hostname):
        return None
    if parsed.username or parsed.password or parsed.port:
        return None
    return url


def link_url(url) -> str | None:
    if not isinstance(url, str) or len(url) > 2000:
        return None
    parsed = urlparse(url)
    return url if parsed.scheme in ("http", "https") and parsed.hostname else None


def _text(value, limit: int) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _aspect(value) -> list[int] | None:
    if isinstance(value, dict):
        w, h = value.get("width"), value.get("height")
    elif isinstance(value, list | tuple) and len(value) == 2:
        w, h = value
    else:
        return None
    if isinstance(w, int) and isinstance(h, int) and 0 < w <= 100_000 and 0 < h <= 100_000:
        return [w, h]
    return None


def clean(platform: str, items) -> list[dict]:
    """Normalize imported media; invalid items are dropped rather than failing the post."""
    if not isinstance(items, list):
        return []
    out, images = [], 0
    for item in items[:8]:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        thumb = media_url(platform, item.get("thumb"))
        if kind in ("image", "video") and thumb and images < MAX_IMAGES:
            entry = {"type": kind, "thumb": thumb, "alt": _text(item.get("alt"), 2000)}
            if kind == "image":
                entry["full"] = media_url(platform, item.get("full")) or thumb
            if aspect := _aspect(item.get("aspect")):
                entry["aspect"] = aspect
            out.append(entry)
            images += 1
        elif kind == "link" and (url := link_url(item.get("url"))) and not any(m["type"] == "link" for m in out):
            out.append(
                {
                    "type": "link",
                    "url": url,
                    "title": _text(item.get("title"), 300),
                    "description": _text(item.get("description"), 1000),
                    "thumb": thumb,
                }
            )
    return out


def dumps(items: list[dict]) -> str:
    return json.dumps(items, ensure_ascii=False, separators=(",", ":"))


def loads(value) -> list[dict]:
    try:
        items = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return items if isinstance(items, list) else []


def _blob_cid(blob) -> str | None:
    ref = (blob or {}).get("ref") if isinstance(blob, dict) else None
    cid = ref.get("$link") if isinstance(ref, dict) else None
    return cid if isinstance(cid, str) and _CID.match(cid) else None


def bluesky_image(did: str, cid: str, preset: str = "feed_thumbnail") -> str:
    return f"https://cdn.bsky.app/img/{preset}/plain/{did}/{cid}@jpeg"


def bluesky_avatar(did: str, blob) -> str | None:
    cid = _blob_cid(blob)
    return bluesky_image(did, cid, "avatar_thumbnail") if cid and _DID.match(did) else None


def bluesky_media(did: str, record: dict) -> list[dict]:
    """Media references from a raw app.bsky.feed.post record (Jetstream or record view)."""
    if not _DID.match(did or ""):
        return []
    embed = record.get("embed") or {}
    if embed.get("$type") == "app.bsky.embed.recordWithMedia":
        embed = embed.get("media") or {}
    kind = embed.get("$type")
    items: list[dict] = []
    if kind == "app.bsky.embed.images":
        for image in (embed.get("images") or [])[:MAX_IMAGES]:
            if cid := _blob_cid(image.get("image")):
                items.append(
                    {
                        "type": "image",
                        "thumb": bluesky_image(did, cid),
                        "full": bluesky_image(did, cid, "feed_fullsize"),
                        "alt": image.get("alt"),
                        "aspect": image.get("aspectRatio"),
                    }
                )
    elif kind == "app.bsky.embed.video":
        if cid := _blob_cid(embed.get("video")):
            items.append(
                {
                    "type": "video",
                    "thumb": f"https://video.cdn.bsky.app/hls/{did}/{cid}/thumbnail.jpg",
                    "alt": embed.get("alt"),
                    "aspect": embed.get("aspectRatio"),
                }
            )
    elif kind == "app.bsky.embed.external":
        external = embed.get("external") or {}
        cid = _blob_cid(external.get("thumb"))
        items.append(
            {
                "type": "link",
                "url": external.get("uri"),
                "title": external.get("title"),
                "description": external.get("description"),
                "thumb": bluesky_image(did, cid) if cid else None,
            }
        )
    return clean("bluesky", items)


def display(items, post_url: str) -> str | None:
    """Stored references in the reader's presentation shape (see packages/web/README.md)."""
    images, external = [], None
    for item in items:
        if item.get("type") == "image":
            images.append({"url": item["thumb"], "full_url": item.get("full") or item["thumb"], "alt": item["alt"]})
        elif item.get("type") == "video":
            images.append({"url": item["thumb"], "full_url": post_url, "alt": item["alt"] or "Video"})
        elif item.get("type") == "link":
            external = {
                "url": item["url"],
                "title": item.get("title", ""),
                "description": item.get("description", ""),
                "image": item.get("thumb"),
            }
    if not images and not external:
        return None
    return dumps({"images": images, "external": external} if external else {"images": images})
