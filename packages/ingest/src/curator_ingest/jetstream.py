"""Jetstream consumer: keeps a rolling window of posts plus engagement counts.

Jetstream is Bluesky's JSON re-encoding of the firehose. We subscribe to posts,
likes and reposts only; everything else (follows, blocks, profiles) is fetched
on demand from the AppView for the handful of users we curate for.

Replay after a reconnect starts a few seconds before the saved cursor. Post
inserts are idempotent; like/repost counters can over-count by the overlap,
which is noise for ranking purposes.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

import websockets
from curator_core import db

from .config import settings
from .prune import prune

log = logging.getLogger(__name__)

POST = "app.bsky.feed.post"
LIKE = "app.bsky.feed.like"
REPOST = "app.bsky.feed.repost"
COLLECTIONS = (POST, LIKE, REPOST)

CURSOR_KEY = "jetstream_cursor"
REPLAY_OVERLAP_US = 5_000_000
FLUSH_EVERY_S = 1.0
PRUNE_EVERY_S = 600


@dataclass
class Batch:
    posts: list[tuple] = field(default_factory=list)
    deletes: list[tuple] = field(default_factory=list)
    likes: dict[str, int] = field(default_factory=dict)
    reposts: dict[str, int] = field(default_factory=dict)
    replies: dict[str, int] = field(default_factory=dict)
    last_time_us: int = 0
    seen: int = 0

    def __len__(self) -> int:
        return self.seen


def _embed(record: dict) -> tuple[str | None, str | None]:
    embed = record.get("embed") or {}
    kind = embed.get("$type", "")
    if kind == "app.bsky.embed.recordWithMedia":
        rec = (embed.get("record") or {}).get("record") or {}
        return "record", rec.get("uri")
    if kind == "app.bsky.embed.record":
        return "record", (embed.get("record") or {}).get("uri")
    if kind == "app.bsky.embed.external":
        return "external", (embed.get("external") or {}).get("uri")
    if kind == "app.bsky.embed.images":
        return "images", None
    if kind == "app.bsky.embed.video":
        return "video", None
    return None, None


def handle_event(evt: dict, batch: Batch, keep_langs: set[str]) -> None:
    batch.last_time_us = evt.get("time_us", batch.last_time_us)
    if evt.get("kind") != "commit":
        return
    commit = evt["commit"]
    coll = commit.get("collection")
    op = commit.get("operation")
    did = evt["did"]
    uri = f"at://{did}/{coll}/{commit.get('rkey')}"
    batch.seen += 1

    if coll == POST:
        if op == "delete":
            batch.deletes.append((uri,))
            return
        if op != "create":
            return
        rec = commit.get("record") or {}
        langs = rec.get("langs") or []
        if keep_langs and not keep_langs.intersection(langs):
            return
        reply = rec.get("reply") or {}
        parent = (reply.get("parent") or {}).get("uri")
        root = (reply.get("root") or {}).get("uri")
        if parent:
            batch.replies[parent] = batch.replies.get(parent, 0) + 1
        kind, ref = _embed(rec)
        batch.posts.append(
            (
                uri,
                commit.get("cid", ""),
                did,
                rec.get("text", ""),
                ",".join(langs),
                rec.get("createdAt"),
                evt["time_us"] // 1_000_000,
                parent,
                root,
                kind,
                ref,
            )
        )
    elif op == "create" and coll in (LIKE, REPOST):
        subject = ((commit.get("record") or {}).get("subject") or {}).get("uri")
        if subject:
            target = batch.likes if coll == LIKE else batch.reposts
            target[subject] = target.get(subject, 0) + 1


def flush(conn, batch: Batch) -> None:
    with db.tx(conn):
        conn.executemany(
            "INSERT OR IGNORE INTO posts(uri, cid, author_did, text, langs, created_at,"
            " indexed_at, reply_parent, reply_root, embed_kind, embed_ref)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            batch.posts,
        )
        conn.executemany("DELETE FROM posts WHERE uri = ?", batch.deletes)
        for col, counts in (("likes", batch.likes), ("reposts", batch.reposts), ("replies", batch.replies)):
            conn.executemany(
                f"UPDATE posts SET {col} = {col} + ? WHERE uri = ?",
                [(n, u) for u, n in counts.items()],
            )
        if batch.last_time_us:
            db.kv_set(conn, CURSOR_KEY, str(batch.last_time_us))


def subscribe_url(base: str, cursor: int | None) -> str:
    params = [("wantedCollections", c) for c in COLLECTIONS]
    if cursor:
        params.append(("cursor", str(cursor - REPLAY_OVERLAP_US)))
    return f"{base}?{urlencode(params)}"


async def run(stats_only: bool = False, duration_s: float | None = None) -> None:
    cfg = settings()
    conn = None if stats_only else db.connect()
    keep_langs = set(cfg.keep_langs)
    started = time.monotonic()
    last_prune = 0.0
    counts: dict[str, int] = {}

    while True:
        cursor = None if conn is None else db.kv_get(conn, CURSOR_KEY)
        url = subscribe_url(cfg.jetstream_url, int(cursor) if cursor else None)
        log.info("connecting %s", url)
        try:
            async with websockets.connect(url, max_size=2**22, ping_interval=30) as ws:
                batch, last_flush = Batch(), time.monotonic()
                async for raw in ws:
                    evt = json.loads(raw)
                    if stats_only:
                        c = evt.get("commit") or {}
                        k = f"{c.get('collection')}:{c.get('operation')}"
                        counts[k] = counts.get(k, 0) + 1
                    else:
                        handle_event(evt, batch, keep_langs)
                    now = time.monotonic()
                    if conn is not None and now - last_flush >= FLUSH_EVERY_S:
                        await asyncio.to_thread(flush, conn, batch)
                        batch, last_flush = Batch(), now
                        if now - last_prune >= PRUNE_EVERY_S:
                            await asyncio.to_thread(prune, conn, cfg.retention_hours)
                            last_prune = now
                    if duration_s and now - started >= duration_s:
                        if conn is not None:
                            flush(conn, batch)
                        if stats_only:
                            elapsed = now - started
                            for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
                                print(f"{k:40s} {v / elapsed:8.1f}/s  ~{v / elapsed * 86400 / 1e6:6.2f}M/day")
                        return
        except (websockets.ConnectionClosed, OSError) as e:
            log.warning("jetstream disconnected: %s; reconnecting in 3s", e)
            await asyncio.sleep(3)
