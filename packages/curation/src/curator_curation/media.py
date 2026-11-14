"""Prepare post media for multimodal public scoring.

Images are fetched only from validated platform media hosts, outside database
transactions. A failed fetch drops that image; the post is still scored on its
text, alt text and link card.
"""

import logging
from concurrent.futures import ThreadPoolExecutor

import httpx
from curator_core.media import media_url
from curator_core.sources import SocialPost

from .config import settings
from .llm.gemini import ScoringImage

log = logging.getLogger(__name__)

MIME_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")
FETCH_WORKERS = 8


def describe(posts: list[SocialPost]) -> tuple[dict[str, tuple[dict, ...]], dict[str, tuple[str, str]]]:
    """Model-facing media per post key, and image_ref -> (platform, url) for images to attach."""
    described, wanted = {}, {}
    for i, post in enumerate(posts):
        items = []
        for j, item in enumerate(post.media):
            if item.get("type") == "link":
                entry = {k: item[k] for k in ("type", "url", "title", "description") if item.get(k)}
            else:
                entry = {"type": item.get("type"), "alt": item.get("alt", "")}
            if item.get("thumb"):
                ref = f"p{i}m{j}"
                entry["image_ref"] = ref
                wanted[ref] = (post.platform, item["thumb"])
            items.append(entry)
        described[post.key] = tuple(items)
    return described, wanted


def _fetch(client: httpx.Client, platform: str, url: str) -> tuple[str, bytes] | None:
    if not media_url(platform, url):
        return None
    limit = settings().max_image_bytes
    try:
        with client.stream("GET", url) as response:
            mime = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if response.status_code != 200 or mime not in MIME_TYPES:
                return None
            if int(response.headers.get("content-length") or 0) > limit:
                return None
            data = b""
            for chunk in response.iter_bytes():
                data += chunk
                if len(data) > limit:
                    return None
            return mime, data
    except (httpx.HTTPError, ValueError):
        return None


def fetch_images(wanted: dict[str, tuple[str, str]]) -> tuple[ScoringImage, ...]:
    if not wanted:
        return ()
    timeout = settings().image_fetch_timeout_s
    with (
        httpx.Client(timeout=timeout, follow_redirects=False) as client,
        ThreadPoolExecutor(min(FETCH_WORKERS, len(wanted))) as pool,
    ):
        results = list(pool.map(lambda ref: (ref, _fetch(client, *wanted[ref])), wanted))
    images = tuple(ScoringImage(ref, got[0], got[1]) for ref, got in results if got)
    if len(images) < len(wanted):
        log.info("loaded %d of %d post images for scoring", len(images), len(wanted))
    return images
