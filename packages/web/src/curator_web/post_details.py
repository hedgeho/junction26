"""Convert public Bluesky post views into safe presentation metadata."""

import json
from urllib.parse import quote, urlsplit


def https_url(value):
    if not isinstance(value, str) or len(value) > 4096:
        return None
    try:
        url = urlsplit(value)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            return None
    except ValueError:
        return None
    return value


def facets(record, text):
    if record.get("text") != text:
        return []
    result = []
    length = len(text.encode("utf-8"))
    for facet in record.get("facets") or []:
        if not isinstance(facet, dict):
            continue
        index = facet.get("index") or {}
        start, end = index.get("byteStart"), index.get("byteEnd")
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= length:
            continue
        for feature in facet.get("features") or []:
            kind = feature.get("$type")
            if kind == "app.bsky.richtext.facet#link":
                url = https_url(feature.get("uri"))
            elif kind == "app.bsky.richtext.facet#mention" and isinstance(feature.get("did"), str):
                url = "https://bsky.app/profile/" + quote(feature["did"], safe=":")
            elif kind == "app.bsky.richtext.facet#tag" and isinstance(feature.get("tag"), str):
                url = "https://bsky.app/hashtag/" + quote(feature["tag"], safe="")
            else:
                continue
            if url:
                result.append({"start": start, "end": end, "url": url})
                break
    return result


def media(embed):
    if not isinstance(embed, dict):
        return [], None
    if embed.get("$type") == "app.bsky.embed.recordWithMedia#view":
        embed = embed.get("media") or {}
    images = []
    if embed.get("$type") == "app.bsky.embed.images#view":
        for image in (embed.get("images") or [])[:4]:
            if not isinstance(image, dict):
                continue
            thumb = https_url(image.get("thumb"))
            full = https_url(image.get("fullsize"))
            if thumb and full:
                images.append({"url": thumb, "full_url": full, "alt": str(image.get("alt") or "")[:2000]})
    external = None
    if embed.get("$type") == "app.bsky.embed.external#view":
        item = embed.get("external") or {}
        url = https_url(item.get("uri"))
        if url:
            external = {
                "url": url,
                "title": str(item.get("title") or "")[:500],
                "description": str(item.get("description") or "")[:1000],
                "image": https_url(item.get("thumb")),
            }
    return images, external


def bluesky_details(view, text):
    author = view.get("author") or {}
    record = view.get("record") or {}
    handle = author.get("handle") if isinstance(author.get("handle"), str) else ""
    name = author.get("displayName") if isinstance(author.get("displayName"), str) else ""
    images, external = media(view.get("embed")) if record.get("text") == text else ([], None)
    return {
        "reply_count": view.get("replyCount")
        if type(view.get("replyCount")) is int and view["replyCount"] >= 0
        else None,
        "author_name": name or handle or None,
        "author_handle": handle or None,
        "author_avatar": https_url(author.get("avatar")),
        "author_url": "https://bsky.app/profile/" + quote(author["did"], safe=":")
        if isinstance(author.get("did"), str)
        else None,
        "facets": facets(record, text),
        "images": images,
        "external": external,
    }


def imported_details(post):
    """Optional pipeline fields are JSON media and facets using the display contract."""

    def parse(value, default):
        if not value:
            return default
        try:
            result = json.loads(value)
        except (ValueError, TypeError):
            return default
        return result if isinstance(result, type(default)) else default

    media_data = parse(post.get("media"), {})
    images = media_data.get("images", [])
    return {
        "author_name": post.get("author"),
        "author_handle": post.get("author_handle"),
        "author_avatar": https_url(post.get("author_avatar")),
        "author_url": None,
        "facets": parse(post.get("facets"), []),
        "images": images if isinstance(images, list) else [],
        "external": media_data.get("external") if isinstance(media_data.get("external"), dict) else None,
    }
