import base64
import json

from curator_core.testing import add_user, insert_post
from curator_curation import digests, profiles, worker
from curator_curation.llm.schemas import Assessment, Assessments
from curator_web.app import create_app
from curator_web.config import settings
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

DID = "did:plc:reader"


def signed_session(did, token="csrf-test"):
    data = base64.b64encode(json.dumps({"did": did, "csrf": token}).encode())
    return TimestampSigner(settings().session_secret).sign(data).decode()


def client_for(did=DID):
    client = TestClient(create_app(), base_url="https://feed.example.com")
    client.cookies.set("session", signed_session(did))
    return client


def seed(conn):
    add_user(conn, DID, "GENERATED_CONFIG_SENTINEL")
    uid = profiles.bootstrap(conn, DID)
    insert_post(conn, "did:plc:human", "1", "Original human post about compilers")

    class Scorer:
        def score(self, request):
            return Assessments(
                items=[
                    Assessment(post_key=p.post_key, decision="include", relevance=0.9, substance=0.8, opportunity=0.7)
                    for p in request.candidates
                ]
            )

    worker.run_cycle(conn, Scorer())
    return uid


def test_reader_exposes_only_sources_not_generated_configuration(conn):
    seed(conn)
    client = client_for()
    response = client.get("/me")
    assert response.status_code == 200
    assert "Original human post" in response.text
    assert "GENERATED_CONFIG_SENTINEL" not in response.text
    assert 'name="csrf"' in response.text
    response = client.get("/api/digest")
    assert "GENERATED_CONFIG_SENTINEL" not in response.text
    assert "reason" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert "GENERATED_CONFIG_SENTINEL" in client.get("/settings/profile/public").text


def test_feedback_is_queued_without_calling_models_and_csrf_is_required(conn):
    seed(conn)
    client = client_for()
    assert client.post("/me/feedback", data={"text": "less hype", "key": "one"}).status_code == 403
    response = client.post(
        "/me/feedback", data={"csrf": "csrf-test", "text": "less hype", "key": "one"}, follow_redirects=False
    )
    assert response.status_code == 303
    row = conn.execute("SELECT * FROM feedback_jobs").fetchone()
    assert row["state"] == "queued"
    assert client.get("/api/jobs/" + row["id"]).json() == {"state": "queued"}
    add_user(conn, "did:plc:other")
    assert client_for("did:plc:other").get("/api/jobs/" + row["id"]).status_code == 404


def test_notes_are_owned_and_deleted_without_editing_originals(conn):
    uid = seed(conn)
    client = client_for()
    data = {"csrf": "csrf-test", "title": "Original title", "text": "Exact original \U0001f98b"}
    assert client.post("/settings/notes", data=data, follow_redirects=False).status_code == 303
    row = conn.execute("SELECT * FROM note_versions").fetchone()
    assert row["text"] == data["text"]
    add_user(conn, "did:plc:other")
    assert (
        client_for("did:plc:other")
        .post("/settings/notes/" + row["id"] + "/delete", data={"csrf": "csrf-test"})
        .status_code
        == 404
    )
    assert (
        client.post(
            "/settings/notes/" + row["id"] + "/delete", data={"csrf": "csrf-test"}, follow_redirects=False
        ).status_code
        == 303
    )
    assert not conn.execute("SELECT 1 FROM note_versions WHERE user_id=?", (uid,)).fetchone()


def test_profile_edit_requires_current_revision_and_escapes_html(conn):
    uid = seed(conn)
    client = client_for()
    revision = profiles.head(conn, uid)["id"]
    body = "</textarea><script>alert('injected')</script>"
    data = {"csrf": "csrf-test", "body": body, "revision": revision}
    assert client.post("/settings/profile/public", data=data, follow_redirects=False).status_code == 303
    assert client.post("/settings/profile/public", data=data).status_code == 409
    response = client.get("/settings")
    assert body not in response.text
    assert "&lt;script&gt;" in response.text
    assert profiles.markdown(conn, uid) == body


def test_settings_blocks_and_dismissal_apply_immediately(conn):
    uid = seed(conn)
    client = client_for()
    post = digests.read(conn, uid)[0]
    data = {"csrf": "csrf-test", "post_key": post["key"]}
    assert client.post("/me/dismiss", data=data, follow_redirects=False).status_code == 303
    assert client.get("/api/digest").json() == {"items": []}
    assert (
        client.post(
            "/settings/delivery", data={"csrf": "csrf-test", "daily_limit": 4, "timezone": "not/a/zone"}
        ).status_code
        == 400
    )
