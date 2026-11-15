"""Retention and follow refresh failures must not revive posts or erase follows."""

import time

import httpx
import pytest
from curator_core import sources
from curator_core.imports import import_posts
from curator_core.testing import add_user, insert_post
from curator_ingest import follows, prune


def test_failed_follow_refresh_preserves_previous_follow_set(conn, monkeypatch):
    add_user(conn, "did:reader", follows=["did:friend"])
    before = tuple(conn.execute("SELECT * FROM follow_sync").fetchone())

    def fail(actor):
        raise httpx.ReadTimeout("Unavailable")

    monkeypatch.setattr(follows, "get_follows", fail)
    with pytest.raises(httpx.ReadTimeout):
        follows.sync_follows(conn, "did:reader")
    assert [tuple(row) for row in conn.execute("SELECT * FROM follows")] == [("did:reader", "did:friend")]
    assert tuple(conn.execute("SELECT * FROM follow_sync").fetchone()) == before


def test_follow_refresh_replaces_previous_set_atomically_and_deduplicates(conn, monkeypatch):
    add_user(conn, "did:reader", follows=["did:old"])
    monkeypatch.setattr(follows, "get_follows", lambda actor: ["did:new", "did:new", "did:friend"])
    follows.sync_follows(conn, "did:reader")
    assert {row[0] for row in conn.execute("SELECT subject_did FROM follows WHERE user_did='did:reader'")} == {
        "did:new",
        "did:friend",
    }


def test_follow_pagination_passes_cursor_and_propagates_incomplete_failure(monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, json={"follows": [{"did": "did:one"}], "cursor": "next"})
        assert request.url.params["cursor"] == "next"
        return httpx.Response(503)

    original = httpx.Client
    monkeypatch.setattr(
        follows.httpx, "Client", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handle))
    )
    with pytest.raises(httpx.HTTPStatusError):
        follows.get_follows("did:reader")
    assert len(requests) == 2


def test_retention_keeps_tombstone_until_legacy_source_expires(conn):
    now = int(time.time())
    uri = insert_post(conn, "did:plc:human", "removed", "Deleted original")
    normalized = {
        "platform": "bluesky",
        "native_id": uri,
        "author_id": "did:plc:human",
        "canonical_url": "https://bsky.app/profile/did:plc:human/post/removed",
        "text": "Deleted original",
        "observed_at": now - 49 * 3600,
        "deleted": True,
    }
    import_posts(conn, [normalized])
    assert prune.prune(conn, 48) == 0
    assert sources.get_post(conn, "bluesky:" + uri) is None
    assert conn.execute("SELECT count(*) FROM social_posts WHERE deleted=1").fetchone()[0] == 1
    conn.execute("UPDATE posts SET indexed_at=? WHERE uri=?", (now - 49 * 3600, uri))
    assert prune.prune(conn, 48) == 1
    assert conn.execute("SELECT count(*) FROM social_posts").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM posts_fts").fetchone()[0] == 0
    assert sources.get_post(conn, "bluesky:" + uri) is None
