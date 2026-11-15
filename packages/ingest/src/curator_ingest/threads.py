"""Collect public Threads posts for readers' public search terms via the Threads keyword search API."""

import asyncio
import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import httpx
from curator_core import db

from .config import settings
from .imports import import_posts

log = logging.getLogger(__name__)

# is_reply needs threads_read_replies; without it Meta fails the whole request with error 10.
SEARCH_FIELDS = (
    "id,text,media_type,media_url,thumbnail_url,permalink,timestamp,username,is_reply,is_quote_post,"
    "quoted_post,alt_text,link_attachment_url,profile_picture_url,"
    "children{media_type,media_url,thumbnail_url,alt_text}"
)
# Meta requires a long-lived token to be at least 24h old to refresh and lives 60 days.
REFRESH_EVERY_S = 7 * 86400


class _RedactToken(logging.Filter):
    # Meta documents the token only as a query parameter, and httpx logs request URLs.
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = re.sub(r"access_token=[^&\s\"]+", "access_token=REDACTED", record.getMessage())
        record.args = None
        return True


logging.getLogger("httpx").addFilter(_RedactToken())


def _created_at(value: object) -> str | None:
    try:
        return datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%S%z").isoformat()
    except ValueError:
        return None


def _media_entries(item: dict) -> list[dict]:
    entries = []
    children = item.get("children")
    data = children.get("data") if isinstance(children, dict) else None
    parts = [item, *data] if isinstance(data, list) else [item]
    for part in filter(lambda p: isinstance(p, dict), parts):
        kind = part.get("media_type")
        if kind == "IMAGE" and part.get("media_url"):
            entries.append(
                {
                    "type": "image",
                    "thumb": part["media_url"],
                    "full": part["media_url"],
                    "alt": part.get("alt_text") or "",
                }
            )
        elif kind == "VIDEO" and part.get("thumbnail_url"):
            entries.append({"type": "video", "thumb": part["thumbnail_url"], "alt": part.get("alt_text") or ""})
    if item.get("link_attachment_url"):
        entries.append({"type": "link", "url": item["link_attachment_url"]})
    return entries


def normalize(item: dict, observed_at: int) -> dict | None:
    """Convert one API post to an import record.

    Args:
        item (dict): A post object from the Threads API.
        observed_at (int): Unix time the post was collected.

    Returns:
        dict | None: The import record, or None for replies, reposts and posts missing identity fields.
    """
    if not isinstance(item, dict) or item.get("is_reply") or item.get("media_type") == "REPOST_FACADE":
        return None
    if not all(isinstance(item.get(k), str) and item[k] for k in ("id", "username", "permalink")):
        return None
    quoted = item.get("quoted_post")
    if isinstance(quoted, dict):
        quoted = quoted.get("id")
    text = item.get("text")
    return {
        "platform": "threads",
        "native_id": item["id"],
        "author_id": item["username"],
        "author_name": item["username"],
        "author_handle": item["username"],
        "author_avatar": item.get("profile_picture_url"),
        "text": text if isinstance(text, str) else "",
        "canonical_url": item["permalink"],
        "created_at": _created_at(item.get("timestamp")),
        "observed_at": observed_at,
        "quote_id": quoted if isinstance(quoted, str) and quoted else None,
        "media": _media_entries(item),
    }


def search_terms(conn: sqlite3.Connection) -> list[str]:
    """Public retrieval terms from active readers' current public profiles.

    Args:
        conn (sqlite3.Connection): The application database.

    Returns:
        list[str]: Case-folded, deduplicated, sorted terms.
    """
    cutoff = int(time.time()) - settings().active_reader_days * 86400
    rows = conn.execute(
        """SELECT r.search_terms FROM users u
        JOIN reader_accounts a ON a.bluesky_did=u.did
        JOIN profile_heads h ON h.user_id=a.id AND h.kind='public'
        JOIN profile_revisions r ON r.id=h.revision_id
        WHERE u.last_seen_at>?""",
        (cutoff,),
    )
    return sorted({t.strip().casefold() for row in rows for t in json.loads(row["search_terms"]) if t.strip()})


def collect_once(conn: sqlite3.Connection, client: httpx.Client, token: str) -> int:
    """Search the next batch of terms and import the results.

    Args:
        conn (sqlite3.Connection): The application database.
        client (httpx.Client): A client whose base URL is the Threads Graph host.
        token (str): The Threads access token.

    Returns:
        int: Number of posts imported.
    """
    cfg = settings()
    db.kv_set(conn, "threads:last_cycle", str(int(time.time())))
    terms = search_terms(conn)
    if not terms:
        return 0
    start = int(db.kv_get(conn, "threads:cursor") or 0) % len(terms)
    batch = [terms[(start + i) % len(terms)] for i in range(min(cfg.threads_terms_per_cycle, len(terms)))]
    total = skipped = done = 0
    for i, term in enumerate(batch):
        now = int(time.time())
        # Clamp so a term that returns after a long absence does not import posts older than retention.
        since = max(int(db.kv_get(conn, f"threads:since:{term}") or 0), now - cfg.retention_hours * 3600)
        params = {
            "q": term,
            "search_type": "RECENT",
            "since": since,
            "limit": 100,
            "fields": SEARCH_FIELDS,
            "access_token": token,
        }
        try:
            response = client.get("/v1.0/keyword_search", params=params)
        except httpx.HTTPError as e:
            done = i + 1
            log.warning("threads search failed: %s", type(e).__name__)
            continue
        if _rate_limited(response):
            log.warning("threads search rate limited; ending cycle")
            break
        done = i + 1
        try:
            response.raise_for_status()
            data = response.json()["data"]
            if not isinstance(data, list):
                raise TypeError("search data is not a list")
        except httpx.HTTPStatusError as e:
            log.warning("threads search failed: HTTP %d", e.response.status_code)
            continue
        except (ValueError, KeyError, TypeError) as e:
            log.warning("threads search failed: %s", type(e).__name__)
            continue
        for record in filter(None, (normalize(i, now) for i in data)):
            try:
                total += import_posts(conn, [record])
            except ValueError:
                skipped += 1
        db.kv_set(conn, f"threads:since:{term}", str(now))
    # Terms cut off by a rate limit stay at the front of the next cycle.
    db.kv_set(conn, "threads:cursor", str((start + done) % len(terms)))
    log.info("threads collected %d posts (%d invalid skipped) for %d terms", total, skipped, done)
    return total


# Graph API throttling codes, which Meta may return as 400/403 instead of 429.
_RATE_LIMIT_CODES = {4, 17, 32, 613}


def _rate_limited(response: httpx.Response) -> bool:
    if response.status_code == 429:
        return True
    if response.status_code not in (400, 403):
        return False
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        return False
    return isinstance(error, dict) and error.get("code") in _RATE_LIMIT_CODES


def configured_token() -> str | None:
    secret = settings().threads_access_token
    return (secret.get_secret_value() if secret else "") or None


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_token(path: Path, configured: str | None) -> str | None:
    """Pick the token to use.

    Args:
        path (Path): The refreshed-token file.
        configured (str | None): The token from the environment.

    Returns:
        str | None: The saved refreshed token, unless the environment token was changed since it was saved.
    """
    if configured is None:
        return None
    saved = _read(path)
    return saved["access_token"] if saved.get("seed") == configured and saved.get("access_token") else configured


def save_token(path: Path, token: str, *, seed: str, now: int) -> None:
    """Atomically write the refreshed token, readable only by the owner.

    Args:
        path (Path): The refreshed-token file.
        token (str): The new access token.
        seed (str): The environment token this refresh descends from.
        now (int): Unix time of the refresh.

    Returns:
        None
    """
    temp = path.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"access_token": token, "seed": seed, "refreshed_at": now}, f)
    os.replace(temp, path)


def refresh_if_due(client: httpx.Client, path: Path, configured: str, now: int) -> str:
    """Refresh the long-lived token when due and return the token to use.

    Args:
        client (httpx.Client): A client whose base URL is the Threads Graph host.
        path (Path): The refreshed-token file.
        configured (str): The token from the environment.
        now (int): Current Unix time.

    Returns:
        str: The refreshed token, or the previous one when refresh is not due or fails.
    """
    token = load_token(path, configured)
    saved = _read(path)
    if saved.get("seed") == configured and now - int(saved.get("refreshed_at", 0)) < REFRESH_EVERY_S:
        return token
    try:
        response = client.get("/refresh_access_token", params={"grant_type": "th_refresh_token", "access_token": token})
        response.raise_for_status()
        fresh = response.json()["access_token"]
    except httpx.HTTPStatusError as e:
        log.warning("threads token refresh failed: HTTP %d", e.response.status_code)
        return token
    except (httpx.HTTPError, ValueError, KeyError) as e:
        log.warning("threads token refresh failed: %s", type(e).__name__)
        return token
    save_token(path, fresh, seed=configured, now=now)
    return fresh


async def run() -> None:
    cfg = settings()
    configured = configured_token()
    if configured is None:
        return
    conn = db.connect()
    path = cfg.data_dir / "threads_token.json"
    with httpx.Client(base_url=cfg.threads_api, timeout=20) as client:
        while True:
            wait = cfg.threads_poll_interval_s
            # A failed cycle must not stop Jetstream and follow sync, which share this process.
            try:
                due_in = await asyncio.to_thread(seconds_until_due, conn, int(time.time()))
                if due_in > 0:
                    wait = due_in
                else:
                    token = await asyncio.to_thread(refresh_if_due, client, path, configured, int(time.time()))
                    await asyncio.to_thread(collect_once, conn, client, token)
            except Exception as e:  # noqa: BLE001
                log.warning("threads cycle failed: %s", type(e).__name__)
            await asyncio.sleep(wait)


def seconds_until_due(conn: sqlite3.Connection, now: int) -> int:
    """Seconds until the next cycle, so restarts do not spend extra search quota.

    Args:
        conn (sqlite3.Connection): The application database.
        now (int): Current Unix time.

    Returns:
        int: Zero or less when a cycle is due.
    """
    last = int(db.kv_get(conn, "threads:last_cycle") or 0)
    return last + settings().threads_poll_interval_s - now
