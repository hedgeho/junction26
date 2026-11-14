import asyncio
from types import SimpleNamespace

import httpx
import pytest
from curator_core.sources import get_post
from curator_core.testing import add_user, insert_post
from curator_curation.candidates import public_candidates
from curator_web import bluesky_feed, oauth

DID = "did:plc:reader"
OTHER = "did:plc:other"


def payload(author="did:plc:writer", key="one", text="Original text\n\nWith original spacing."):
    return {
        "feed": [
            {
                "post": {
                    "uri": f"at://{author}/app.bsky.feed.post/{key}",
                    "author": {"did": author, "handle": "writer.bsky.social"},
                    "record": {"text": text, "createdAt": "2026-11-14T10:00:00Z"},
                }
            }
        ]
    }


def run_sync(conn, monkeypatch, response, did=DID):
    async def fetch(requested_did):
        assert requested_did == did
        return response

    monkeypatch.setattr(bluesky_feed, "fetch_feed", fetch)
    asyncio.run(bluesky_feed.sync(did, initialize=True))


def test_feed_original_text_deduplication_and_reader_scoping(conn, monkeypatch):
    add_user(conn, DID)
    add_user(conn, OTHER)
    body = payload()
    body["feed"].append(body["feed"][0])
    run_sync(conn, monkeypatch, body)
    run_sync(conn, monkeypatch, payload("did:plc:someone", "two"), OTHER)
    insert_post(conn, "did:plc:firehose", "unrelated", "Unrelated firehose post")
    source = get_post(conn, "bluesky:" + body["feed"][0]["post"]["uri"])
    assert source.text == "Original text\n\nWith original spacing."
    assert bluesky_feed.status(conn, DID)["post_count"] == 1
    own = public_candidates(conn, DID, [], 20)
    other = public_candidates(conn, OTHER, [], 20)
    assert {p.author_id for p in own} == {"did:plc:writer", "did:plc:someone", "did:plc:firehose"}
    assert {p.author_id for p in other} == {p.author_id for p in own}


def test_authentication_notice_does_not_become_a_candidate(conn, monkeypatch):
    add_user(conn, DID)
    body = payload(text="This feed requires authentication.")
    body["feed"][0]["post"]["uri"] = bluesky_feed.AUTH_NOTICE_URI
    run_sync(conn, monkeypatch, body)
    assert bluesky_feed.status(conn, DID)["state"] == "auth_required"
    assert not conn.execute("SELECT 1 FROM social_posts").fetchone()
    assert not public_candidates(conn, DID, [], 20)


def test_failed_refresh_preserves_previous_snapshot_without_error_payload(conn, monkeypatch):
    add_user(conn, DID)
    run_sync(conn, monkeypatch, payload())

    async def failed(did):
        raise RuntimeError("SECRET_PROVIDER_PAYLOAD")

    monkeypatch.setattr(bluesky_feed, "fetch_feed", failed)
    asyncio.run(bluesky_feed.sync(DID, initialize=True))
    state = bluesky_feed.status(conn, DID)
    assert state["state"] == "failed"
    assert "SECRET_PROVIDER_PAYLOAD" not in str(state)
    assert len(public_candidates(conn, DID, [], 20)) == 1


def test_refresh_is_bounded_and_old_oauth_sessions_are_not_silently_used(conn, monkeypatch):
    add_user(conn, DID)
    calls = []

    async def fetch(did):
        calls.append(did)
        return payload()

    monkeypatch.setattr(bluesky_feed, "fetch_feed", fetch)
    asyncio.run(bluesky_feed.sync(DID))
    assert calls == []
    asyncio.run(bluesky_feed.sync(DID, initialize=True))
    asyncio.run(bluesky_feed.sync(DID))
    assert calls == [DID]
    conn.execute("UPDATE bluesky_feed_sync SET last_attempt=0,state='auth_required'")
    asyncio.run(bluesky_feed.sync(DID))
    assert calls == [DID]


def test_authenticated_read_goes_to_readers_pds_with_appview_proxy(monkeypatch):
    class OAuth:
        async def restore(self, did):
            assert did == DID
            return SimpleNamespace(pds_endpoint="https://pds.example", httpx_auth=None)

    original = httpx.AsyncClient

    def handle(request):
        assert request.url.host == "pds.example"
        assert request.url.path == "/xrpc/app.bsky.feed.getFeed"
        assert request.headers["atproto-proxy"] == bluesky_feed.APPVIEW_SERVICE
        assert request.url.params["feed"] == bluesky_feed.FEED_URI
        assert request.url.params["limit"] == "100"
        return httpx.Response(200, json=payload())

    monkeypatch.setattr(oauth, "client", lambda: OAuth())
    monkeypatch.setattr(
        bluesky_feed.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs)
    )
    assert asyncio.run(bluesky_feed.fetch_feed(DID)) == payload()


def test_source_controls_require_login_and_csrf(conn):
    from curator_web.app import create_app
    from fastapi.testclient import TestClient

    client = TestClient(create_app())
    for path in ("/settings/bluesky/connect", "/settings/bluesky/sync"):
        assert client.post(path).status_code == 403


def test_oauth_requests_specific_interaction_scopes():
    assert "rpc:app.bsky.feed.getFeed?" in oauth.SCOPE
    assert "rpc:app.bsky.feed.getFeedSkeleton?" in oauth.SCOPE
    assert "repo:app.bsky.feed.like?action=create&action=delete" in oauth.SCOPE
    assert "repo:app.bsky.feed.repost?action=create&action=delete" in oauth.SCOPE
    assert "repo:app.bsky.feed.post?action=create" in oauth.SCOPE
    assert "transition:" not in oauth.SCOPE
    assert "repo:*" not in oauth.SCOPE


def test_invalid_identity_is_not_imported(conn, monkeypatch):
    add_user(conn, DID)
    body = payload()
    body["feed"][0]["post"]["author"]["did"] = "did:plc:someoneelse"
    run_sync(conn, monkeypatch, body)
    assert bluesky_feed.status(conn, DID)["state"] == "failed"
    assert not conn.execute("SELECT 1 FROM social_posts").fetchone()


def test_snapshot_replaces_membership_without_treating_omissions_as_source_deletion(conn, monkeypatch):
    add_user(conn, DID)
    run_sync(conn, monkeypatch, payload())
    run_sync(conn, monkeypatch, {"feed": []})
    assert bluesky_feed.status(conn, DID)["state"] == "ready"
    assert public_candidates(conn, DID, [], 20)
    assert conn.execute("SELECT 1 FROM social_posts").fetchone()


def test_invalid_response_rejects_instead_of_using_public_fallback():
    with pytest.raises(ValueError):
        bluesky_feed.normalize_feed({"error": "AuthenticationRequired"})


def test_for_you_keeps_x_candidates_and_honors_source_tombstones(conn, monkeypatch):
    from curator_core.imports import import_posts

    add_user(conn, DID)
    run_sync(conn, monkeypatch, payload())
    import_posts(
        conn,
        [
            {
                "platform": "x",
                "native_id": "123",
                "author_id": "x-human",
                "text": "Original X candidate",
                "canonical_url": "https://x.com/i/status/123",
            }
        ],
    )
    assert {post.platform for post in public_candidates(conn, DID, [], 20)} == {"bluesky", "x"}
    conn.execute("UPDATE social_posts SET deleted=1 WHERE platform='bluesky'")
    assert [post.platform for post in public_candidates(conn, DID, [], 20)] == ["x"]


@pytest.mark.parametrize("reply", [None, {"parent": None, "root": None}])
@pytest.mark.parametrize("embed", [None, {"record": None}, {"record": {"record": None}}])
def test_null_optional_metadata_keeps_original_post(reply, embed):
    body = payload(text="Exact original text with null metadata.")
    body["feed"][0]["post"]["record"].update(reply=reply, embed=embed)
    records = bluesky_feed.normalize_feed(body)
    assert len(records) == 1
    assert records[0]["text"] == "Exact original text with null metadata."
    assert records[0]["parent_id"] is None
    assert records[0]["root_id"] is None
    assert records[0]["quote_id"] is None
