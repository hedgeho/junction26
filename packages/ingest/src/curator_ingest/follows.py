"""Keep `follows` fresh for active readers, from the public AppView."""

import asyncio
import logging
import time

import httpx
from curator_core import db

from .config import settings

log = logging.getLogger(__name__)


def get_follows(actor: str, max_pages: int = 50) -> list[str]:
    dids, cursor = [], None
    with httpx.Client(base_url=settings().public_appview, timeout=20) as c:
        for _ in range(max_pages):
            params = {"actor": actor, "limit": 100}
            if cursor:
                params["cursor"] = cursor
            r = c.get("/xrpc/app.bsky.graph.getFollows", params=params)
            r.raise_for_status()
            body = r.json()
            dids += [f["did"] for f in body.get("follows", [])]
            cursor = body.get("cursor")
            if not cursor:
                break
    return dids


def sync_follows(conn, did: str) -> int:
    follows = get_follows(did)
    with db.tx(conn):
        conn.execute("DELETE FROM follows WHERE user_did = ?", (did,))
        conn.executemany("INSERT OR IGNORE INTO follows VALUES (?, ?)", [(did, f) for f in follows])
        conn.execute(
            "INSERT INTO follow_sync VALUES (?, ?) ON CONFLICT(user_did) DO UPDATE SET synced_at = excluded.synced_at",
            (did, int(time.time())),
        )
    log.info("synced %d follows for %s", len(follows), did)
    return len(follows)


def stale_readers(conn) -> list[str]:
    cfg = settings()
    now = int(time.time())
    rows = conn.execute(
        """SELECT u.did FROM users u LEFT JOIN follow_sync s ON s.user_did = u.did
           WHERE u.last_seen_at > ? AND (s.synced_at IS NULL OR s.synced_at < ?)""",
        (now - cfg.active_reader_days * 86400, now - cfg.follows_ttl_s),
    ).fetchall()
    return [r["did"] for r in rows]


async def run() -> None:
    conn = db.connect()
    while True:
        for did in await asyncio.to_thread(stale_readers, conn):
            try:
                await asyncio.to_thread(sync_follows, conn, did)
            except httpx.HTTPError as e:
                log.warning("follow sync failed for %s: %s", did, e)
        await asyncio.sleep(settings().follows_check_interval_s)
