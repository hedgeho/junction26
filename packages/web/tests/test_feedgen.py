from curator_curation.rank import GLOBAL
from curator_web.app import create_app
from curator_web.config import settings
from fastapi.testclient import TestClient


def test_feed_endpoints(conn):
    conn.execute("INSERT INTO feed_items VALUES (?, 0, 'at://x/app.bsky.feed.post/1', NULL)", (GLOBAL,))
    client = TestClient(create_app())
    feed_uri = settings().feed_uri
    assert client.get("/.well-known/did.json").json()["id"] == "did:web:feed.example.com"
    assert client.get("/xrpc/app.bsky.feed.describeFeedGenerator").json()["feeds"][0]["uri"] == feed_uri
    r = client.get("/xrpc/app.bsky.feed.getFeedSkeleton", params={"feed": feed_uri, "limit": 1})
    assert r.json() == {"feed": []}
    assert client.get("/xrpc/app.bsky.feed.getFeedSkeleton", params={"feed": "at://nope"}).status_code == 400
    bad_auth = {"Authorization": "Bearer garbage"}
    r = client.get("/xrpc/app.bsky.feed.getFeedSkeleton", params={"feed": feed_uri}, headers=bad_auth)
    assert r.status_code == 200  # invalid token = anonymous, gets an empty feed
    assert client.post("/xrpc/app.bsky.feed.sendInteractions", json={}, headers=bad_auth).status_code == 401
