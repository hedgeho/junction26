"""Accepted feedback remains recoverable through its reader-scoped submission key."""

import base64
import json

import pytest
from curator_core import db
from curator_core.testing import add_user, insert_post
from curator_curation import jobs, profiles
from curator_curation.llm.schemas import ProfileProposal
from curator_web.app import create_app
from curator_web.config import settings
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner


def client(did):
    browser = TestClient(create_app(), base_url="https://feed.example.com")
    cookie = base64.b64encode(json.dumps({"did": did, "csrf": "csrf-test"}).encode())
    browser.cookies.set("session", TimestampSigner(settings().session_secret).sign(cookie).decode())
    return browser


@pytest.mark.parametrize("remove_source", [False, True])
def test_post_feedback_receipt_recovered_by_submission_key(conn, remove_source):
    did = "did:plc:feedback-recovery"
    add_user(conn, did)
    uid = profiles.bootstrap(conn, did)
    db.kv_set(conn, "setup:" + uid, "done")
    uri = insert_post(conn, "did:plc:compiler", "test", "Detailed compiler benchmarks")
    browser = client(did)
    response = browser.post(
        "/me/feedback",
        data={
            "csrf": "csrf-test",
            "key": "recover-this-post",
            "text": "More reproducible benchmarks",
            "post_key": "bluesky:" + uri,
        },
        headers={"accept": "application/json"},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    assert browser.get("/api/feedback-status", params={"key": "recover-this-post"}).json() == {"state": "queued"}
    if remove_source:
        conn.execute("DELETE FROM posts WHERE uri=?", (uri,))

    class Model:
        def update_profile(self, current, feedback, terms=(), **context):
            assert "Detailed compiler benchmarks" in feedback
            assert context["user_instruction"] == "More reproducible benchmarks"
            return ProfileProposal(
                markdown=current + "\n- [topic] Prefer reproducible compiler benchmarks\n", search_terms=list(terms)
            )

    assert jobs.process_one(conn, Model(), include_notes=False)
    recovered = browser.get("/api/feedback-status", params={"key": "recover-this-post"})
    assert recovered.status_code == 200
    receipt = recovered.json()
    assert receipt == browser.get("/api/jobs/" + job_id).json()
    assert receipt["state"] == "applied" and receipt["job_id"] == job_id
    assert receipt["post_key"] == "bluesky:" + uri
    assert receipt["diff"]["changed"] and receipt["changes"]
    assert any(line["type"] == "add" and "reproducible" in line["text"] for line in receipt["diff"]["lines"])
    add_user(conn, "did:plc:another-reader")
    assert (
        client("did:plc:another-reader").get("/api/feedback-status", params={"key": "recover-this-post"}).status_code
        == 404
    )
    assert browser.get("/api/feedback-status", params={"key": "missing"}).status_code == 404


def test_recovery_key_is_bounded_and_required(conn):
    add_user(conn, "did:plc:recovery-bounds")
    browser = client("did:plc:recovery-bounds")
    assert browser.get("/api/feedback-status").status_code == 422
    assert browser.get("/api/feedback-status", params={"key": "x" * 129}).status_code == 422


def test_accepted_post_feedback_can_be_retried_after_source_disappears(conn):
    did = "did:plc:feedback-replay"
    add_user(conn, did)
    uid = profiles.bootstrap(conn, did)
    db.kv_set(conn, "setup:" + uid, "done")
    uri = insert_post(conn, "did:plc:original", "replay", "Careful technical writing")
    browser = client(did)
    data = {
        "csrf": "csrf-test",
        "key": "accepted-before-deletion",
        "text": "More careful technical writing",
        "post_key": "bluesky:" + uri,
    }
    first = browser.post("/me/feedback", data=data, headers={"accept": "application/json"})
    assert first.status_code == 202
    conn.execute("DELETE FROM posts WHERE uri=?", (uri,))
    replay = browser.post("/me/feedback", data=data, headers={"accept": "application/json"})
    assert replay.status_code == 202
    assert replay.json()["job_id"] == first.json()["job_id"]
    assert conn.execute("SELECT count(*) FROM feedback_jobs WHERE user_id=?", (uid,)).fetchone()[0] == 1
    assert (
        "Careful technical writing"
        in conn.execute("SELECT post_context FROM feedback_jobs WHERE user_id=?", (uid,)).fetchone()[0]
    )
    assert (
        browser.post(
            "/me/feedback", data=data | {"key": "new-after-deletion"}, headers={"accept": "application/json"}
        ).status_code
        == 404
    )


def test_concurrent_post_feedback_replays_create_one_durable_job(conn):
    from concurrent.futures import ThreadPoolExecutor

    did = "did:plc:feedback-concurrent"
    add_user(conn, did)
    uid = profiles.bootstrap(conn, did)
    db.kv_set(conn, "setup:" + uid, "done")
    uri = insert_post(conn, "did:plc:original", "concurrent", "Careful technical writing")
    data = {
        "csrf": "csrf-test",
        "key": "one-concurrent-submission",
        "text": "More careful technical writing",
        "post_key": "bluesky:" + uri,
    }

    def submit(_):
        response = client(did).post("/me/feedback", data=data, headers={"accept": "application/json"})
        assert response.status_code == 202
        return response.json()["job_id"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        identifiers = list(pool.map(submit, range(8)))
    assert len(set(identifiers)) == 1
    assert conn.execute("SELECT count(*) FROM feedback_jobs WHERE user_id=?", (uid,)).fetchone()[0] == 1
