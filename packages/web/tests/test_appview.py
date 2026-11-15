"""Public rendering reads must keep batch limits, ancestors and failures intact."""

import httpx
import pytest
from curator_web import appview


def install_client(monkeypatch, handler):
    monkeypatch.setattr(
        appview,
        "client",
        lambda: httpx.Client(base_url="https://appview.example", transport=httpx.MockTransport(handler)),
    )


def test_post_hydration_batches_uris_and_preserves_all_returned_posts(monkeypatch):
    batches = []
    uris = [f"at://did:plc:author/app.bsky.feed.post/{i}" for i in range(51)]

    def handle(request):
        assert request.url.path == "/xrpc/app.bsky.feed.getPosts"
        batch = request.url.params.get_list("uris")
        batches.append(batch)
        return httpx.Response(
            200, json={"posts": [{"uri": uri, "author": {"handle": "human.example"}} for uri in batch]}
        )

    install_client(monkeypatch, handle)
    posts = appview.get_posts(uris)
    assert [len(batch) for batch in batches] == [25, 25, 1]
    assert [post["uri"] for post in posts] == uris
    assert all(post["author"]["handle"] == "human.example" for post in posts)


def test_hydration_failure_does_not_return_a_misleading_partial_success(monkeypatch):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"posts": [{"uri": "first"}]}) if len(calls) == 1 else httpx.Response(503)

    install_client(monkeypatch, handle)
    with pytest.raises(httpx.HTTPStatusError):
        appview.get_posts([str(i) for i in range(26)])
    assert len(calls) == 2


def test_thread_requests_one_branch_with_ancestors_for_incremental_replies(monkeypatch):
    thread = {"post": {"uri": "at://root"}, "parent": {"post": {"uri": "at://parent"}}, "replies": []}

    def handle(request):
        assert request.url.path == "/xrpc/app.bsky.feed.getPostThread"
        assert dict(request.url.params) == {"uri": "at://root", "depth": "1", "parentHeight": "1000"}
        return httpx.Response(200, json={"thread": thread})

    install_client(monkeypatch, handle)
    assert appview.get_thread("at://root") == thread


def test_handle_resolution_and_profile_identity_are_preserved(monkeypatch):
    def handle(request):
        if request.url.path.endswith("resolveHandle"):
            assert request.url.params["handle"] == "human.example"
            return httpx.Response(200, json={"did": "did:plc:human"})
        assert request.url.params["actor"] == "did:plc:human"
        return httpx.Response(
            200, json={"displayName": "Human", "handle": "human.example", "avatar": "https://cdn.example/a"}
        )

    install_client(monkeypatch, handle)
    did = appview.resolve_handle("@human.example")
    assert appview.get_profile(did)["displayName"] == "Human"
