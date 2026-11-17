import json

import pytest
from curator_core import db
from curator_core.config import clear_settings
from curator_core.testing import add_user, insert_post
from curator_curation import profiles, worker
from curator_curation.llm.schemas import Assessment, Assessments
from curator_web import appview
from curator_web.app import create_app
from curator_web.config import settings
from curator_web.site import ARCHIVED_FUNNEL_KEY
from fastapi.testclient import TestClient

DEMO = "did:plc:demo"


class Scorer:
    def score(self, request):
        return Assessments(
            items=[
                Assessment(post_key=p.post_key, decision="include", relevance=0.9, substance=0.8, opportunity=0.7)
                for p in request.candidates
            ]
        )


@pytest.fixture
def archive(conn, monkeypatch):
    add_user(conn, DEMO, "interests")
    uid = profiles.bootstrap(conn, DEMO)
    insert_post(conn, "did:plc:human", "1", "Archived post about compilers")
    db.kv_set(conn, "setup:" + uid, "done")
    worker.run_cycle(conn, Scorer())
    monkeypatch.setattr(appview, "get_posts", lambda uris: [])
    monkeypatch.setenv("DEMO_DID", DEMO)
    monkeypatch.setenv("ARCHIVED_ON", "2026-11-15")
    clear_settings()
    return TestClient(create_app(), base_url="https://feed.example.com", follow_redirects=False)


def test_archive_serves_landing_and_demo_without_sign_in(archive):
    landing = archive.get("/").text
    assert 'action="/login"' not in landing
    assert 'href="/demo"' in landing
    assert "2026-11-15" in landing
    demo = archive.get("/demo")
    assert demo.status_code == 200
    assert "Archived post about compilers" in demo.text
    assert "/#join" not in demo.text
    assert archive.get("/api/demo/post-details").status_code == 200
    assert archive.get("/privacy").status_code == 200


def test_archive_closes_sign_in_reader_pages_and_writes(archive):
    assert archive.get("/me").headers["location"] == "/demo"
    assert archive.get("/settings").headers["location"] == "/demo"
    for path in ("/login", "/me/feedback", "/xrpc/app.bsky.feed.sendInteractions", "/threads/data-deletion"):
        assert archive.post(path).status_code == 410
    for path in ("/api/digest", "/api/funnel", "/oauth/callback", "/oauth-client-metadata.json"):
        assert archive.get(path).status_code == 410


def test_archive_serves_stored_funnel(conn, archive):
    assert archive.get("/api/demo/funnel").json()["stages"] == []
    stored = {"stages": [{"key": "delivered", "label": "In today's feed", "count": 7, "drops": []}], "check": None}
    db.kv_set(conn, ARCHIVED_FUNNEL_KEY, json.dumps(stored))
    assert archive.get("/api/demo/funnel").json() == stored


def test_archive_feed_serves_demo_picks_to_anyone_without_writing(conn, archive):
    users = conn.execute("SELECT count(*) FROM users").fetchone()[0]
    r = archive.get("/xrpc/app.bsky.feed.getFeedSkeleton", params={"feed": settings().feed_uri})
    assert [item["post"] for item in r.json()["feed"]] == ["at://did:plc:human/app.bsky.feed.post/1"]
    assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == users
