import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from curator_web import social

DID = "did:plc:reader"
URI = "at://did:plc:author/app.bsky.feed.post/abc"


@pytest.mark.parametrize("action", ["like", "repost", "save", "comment", "quote"])
def test_social_interactions_use_user_session_and_source_reference(monkeypatch, action):
    calls = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"posts": [{"uri": URI, "cid": "cid123", "viewer": {}, "record": {}}]})
        calls.append((request.url.path, json.loads(request.content), request.headers.get("atproto-proxy")))
        return httpx.Response(200, json={"uri": f"at://{DID}/app.bsky.feed.post/new"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        social.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handler))
    )

    class OAuth:
        async def restore(self, did):
            assert did == DID
            return SimpleNamespace(pds_endpoint="https://pds.example", httpx_auth=None)

    monkeypatch.setattr(social.oauth, "client", lambda: OAuth())
    result = asyncio.run(social.interact(DID, URI, action, "My words"))
    assert result["active"] is True
    if action in ("comment", "quote"):
        assert result["record_uri"] == f"at://{DID}/app.bsky.feed.post/new"
    path, body, proxy = calls[0]
    ref = {"uri": URI, "cid": "cid123"}
    if action == "save":
        assert path.endswith("app.bsky.bookmark.createBookmark") and body == ref and proxy == social.APPVIEW
    else:
        assert path.endswith("com.atproto.repo.createRecord") and body["repo"] == DID
        record = body["record"]
        if action in ("like", "repost"):
            assert record["subject"] == ref
        elif action == "comment":
            assert record["text"] == "My words" and record["reply"] == {"root": ref, "parent": ref}
        else:
            assert record["text"] == "My words" and record["embed"]["record"] == ref


def test_undo_repost_only_deletes_own_record(monkeypatch):
    calls = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "posts": [
                        {"uri": URI, "cid": "cid123", "viewer": {"repost": f"at://{DID}/app.bsky.feed.repost/xyz"}}
                    ]
                },
            )
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        social.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handler))
    )

    class OAuth:
        async def restore(self, did):
            return SimpleNamespace(pds_endpoint="https://pds.example", httpx_auth=None)

    monkeypatch.setattr(social.oauth, "client", lambda: OAuth())
    assert asyncio.run(social.interact(DID, URI, "repost")) == {"active": False}
    assert calls == [{"repo": DID, "collection": "app.bsky.feed.repost", "rkey": "xyz"}]


def test_delete_comment_checks_owner_and_parent_before_deleting(monkeypatch):
    calls = []
    record_uri = f"at://{DID}/app.bsky.feed.post/reply"
    parent = {"uri": URI}

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"uri": record_uri, "cid": "mycid", "value": {"reply": {"parent": parent}}})
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        social.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handler))
    )

    class OAuth:
        async def restore(self, did):
            return SimpleNamespace(pds_endpoint="https://pds.example", httpx_auth=None)

    monkeypatch.setattr(social.oauth, "client", lambda: OAuth())
    asyncio.run(social.delete_comment(DID, URI, record_uri))
    assert calls == [{"repo": DID, "collection": "app.bsky.feed.post", "rkey": "reply", "swapRecord": "mycid"}]
    parent["uri"] = "at://someone/else"
    with pytest.raises(ValueError):
        asyncio.run(social.delete_comment(DID, URI, record_uri))
    with pytest.raises(ValueError):
        asyncio.run(social.delete_comment(DID, URI, "at://did:plc:other/app.bsky.feed.post/reply"))
    assert len(calls) == 1


def test_delete_quote_checks_target_and_reads_without_oauth(monkeypatch):
    calls = []
    quote_uri = f"at://{DID}/app.bsky.feed.post/quote"
    subject = {"uri": URI}
    client_auth = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(
                200, json={"uri": quote_uri, "cid": "quote-cid", "value": {"embed": {"record": subject}}}
            )
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={})

    real_client = httpx.AsyncClient

    def client(**kwargs):
        client_auth.append(kwargs.get("auth"))
        return real_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(social.httpx, "AsyncClient", client)
    auth = object()

    class OAuth:
        async def restore(self, did):
            return SimpleNamespace(pds_endpoint="https://pds.example", httpx_auth=auth)

    monkeypatch.setattr(social.oauth, "client", lambda: OAuth())
    asyncio.run(social.delete_quote(DID, URI, quote_uri))
    assert client_auth == [None, auth]
    assert calls == [{"repo": DID, "collection": "app.bsky.feed.post", "rkey": "quote", "swapRecord": "quote-cid"}]
    subject["uri"] = "at://did:plc:other/app.bsky.feed.post/other"
    with pytest.raises(ValueError):
        asyncio.run(social.delete_quote(DID, URI, quote_uri))
    assert len(calls) == 1
