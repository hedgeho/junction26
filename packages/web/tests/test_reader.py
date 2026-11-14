import base64
import json

from curator_core import db
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
    uri = insert_post(conn, "did:plc:human", "1", "Original human post about compilers")
    db.kv_set(conn, "setup:" + uid, "done")
    conn.execute("INSERT INTO bluesky_feed_sync VALUES (?,'feed','ready',unixepoch(),unixepoch(),1)", (DID,))
    conn.execute("INSERT INTO bluesky_feed_posts VALUES (?,?,0,unixepoch())", (DID, "bluesky:" + uri))

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


def test_markdown_folder_is_read_only_and_can_be_disconnected(conn, tmp_path, monkeypatch):
    uid = seed(conn)
    folder = tmp_path / "notes"
    folder.mkdir()
    original = b"# Original title\r\nExact original writing.\r\n"
    (folder / "thought.md").write_bytes(original)
    monkeypatch.setenv("AGORA_NOTES_ROOTS", str(folder))
    from curator_core.config import clear_settings

    clear_settings()
    client = client_for()
    data = {"csrf": "csrf-test", "enabled": "yes", "folder": str(folder)}
    assert client.post("/settings/notes/folder", data=data, follow_redirects=False).status_code == 303
    row = conn.execute("SELECT * FROM note_versions").fetchone()
    assert row["text"].encode() == original
    assert row["user_id"] == uid
    assert client.post("/settings/notes/folder", data={"csrf": "csrf-test"}, follow_redirects=False).status_code == 303
    assert not conn.execute("SELECT 1 FROM note_versions WHERE user_id=?", (uid,)).fetchone()
    assert (folder / "thought.md").read_bytes() == original


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


def test_first_launch_requires_setup_and_does_not_expose_unconnected_posts(conn):
    add_user(conn, DID)
    client = client_for()
    response = client.get("/me", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/setup"
    assert client.get("/api/digest").json() == {"items": []}
    conn.execute("INSERT INTO bluesky_feed_sync VALUES (?,'feed','ready',unixepoch(),unixepoch(),0)", (DID,))
    assert client.post("/setup", data={"csrf": "csrf-test"}, follow_redirects=False).status_code == 303
    response = client.get("/me")
    assert "No posts yet." in response.text or "Filtering is not configured." in response.text
    assert "sample" not in response.text.lower()


def test_post_feedback_keeps_context_and_rejects_another_readers_post(conn):
    uid = seed(conn)
    client = client_for()
    key = digests.read(conn, uid)[0]["key"]
    data = {
        "csrf": "csrf-test",
        "text": "More technical depth, fewer announcements",
        "key": "post-feedback",
        "post_key": key,
    }
    assert client.post("/me/feedback", data=data, follow_redirects=False).status_code == 303
    row = conn.execute("SELECT * FROM feedback_jobs WHERE user_id=?", (uid,)).fetchone()
    assert row["post_key"] == key
    assert row["feedback"] == data["text"]
    data["post_key"] = "bluesky:unrelated"
    data["key"] = "other"
    assert client.post("/me/feedback", data=data).status_code == 404


def test_removed_feed_membership_does_not_hide_previously_selected_post(conn):
    uid = seed(conn)
    assert digests.read(conn, uid)
    conn.execute("DELETE FROM bluesky_feed_posts WHERE user_did=?", (DID,))
    assert digests.read(conn, uid)


def test_missing_filtering_configuration_is_explicit_not_fake_progress(conn, monkeypatch):
    from curator_core.config import clear_settings

    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("GEMINI_MODEL", "")
    clear_settings()
    seed(conn)
    client = client_for()
    status = client.get("/api/curation")
    assert status.json()["state"] == "not_configured"
    assert "Filtering is not configured." in client.get("/me").text
    setup = client.get("/setup").text
    assert ">Connected</span>" in setup
    assert "GEMINI_API_KEY" in client.get("/settings").text
    assert client.get("/setup").headers["cache-control"] == "no-store"


def test_worker_offline_processing_and_failure_are_visible(conn, monkeypatch):
    from curator_core.config import clear_settings
    from curator_curation import health

    monkeypatch.setenv("GEMINI_API_KEY", "test-only-unused-key")
    monkeypatch.setenv("GEMINI_MODEL", "test-only-model")
    clear_settings()
    uid = seed(conn)
    conn.execute("UPDATE reader_settings SET refresh_requested=1 WHERE user_id=?", (uid,))
    conn.execute("DELETE FROM kv WHERE key='curation-heartbeat'")
    assert health.filtering_status(conn, uid)["state"] == "offline"
    conn.execute("INSERT INTO kv VALUES ('curation-heartbeat', CAST(unixepoch() AS TEXT))")
    assert health.filtering_status(conn, uid)["state"] == "processing"
    db.kv_set(conn, "curation-state:" + uid, "failed")
    assert health.filtering_status(conn, uid)["state"] == "failed"


def test_failed_sign_in_preserves_handle_and_renders_safe_error(conn, monkeypatch):
    from curator_web import oauth

    class Unavailable:
        async def authorize(self, handle):
            raise RuntimeError("PRIVATE_PROVIDER_ERROR")

    monkeypatch.setattr(oauth, "client", lambda: Unavailable())
    client = client_for()
    response = client.post("/login", data={"csrf": "csrf-test", "handle": "my-handle.example"})
    assert response.status_code == 400
    assert 'value="my-handle.example"' in response.text
    assert 'role="alert"' in response.text
    assert "PRIVATE_PROVIDER_ERROR" not in response.text
    assert 'aria-invalid="true"' in response.text


def test_failed_folder_connection_preserves_user_input(conn):
    seed(conn)
    client = client_for()
    value = "missing-folder-selected-by-user"
    for path in ("/setup", "/settings/notes/folder"):
        response = client.post(path, data={"csrf": "csrf-test", "enabled": "yes", "folder": value})
        assert response.status_code == 400
        assert f'value="{value}"' in response.text
        assert 'role="alert"' in response.text
        assert 'name="enabled" value="yes" data-toggle="notes-fields" checked' in response.text


def test_browser_errors_are_readable_and_api_errors_stay_json(conn):
    seed(conn)
    client = client_for()
    error = client.post("/me/feedback", data={"text": "feedback", "key": "one"})
    assert error.status_code == 403
    assert "Session expired" in error.text
    assert "Back to feed" in error.text
    missing = client.get("/api/jobs/not-owned")
    assert missing.status_code == 404
    assert missing.headers["content-type"].startswith("application/json")


def test_static_interactions_and_styles_are_served_without_source_data(conn):
    client = client_for()
    for path in ("/static/app.css", "/static/app.js"):
        response = client.get(path)
        assert response.status_code == 200
        assert "GENERATED_CONFIG_SENTINEL" not in response.text


def test_new_reader_gets_first_feed_posts_and_can_give_feedback(conn):
    add_user(conn, DID)
    uid = profiles.bootstrap(conn, DID)
    db.kv_set(conn, "setup:" + uid, "done")
    conn.execute("INSERT INTO bluesky_feed_sync VALUES (?,'feed','ready',unixepoch(),unixepoch(),12)", (DID,))
    keys = []
    for position in range(12):
        uri = insert_post(conn, "did:plc:human", str(position), f"Original post {position}")
        key = "bluesky:" + uri
        keys.append(key)
        conn.execute("INSERT INTO bluesky_feed_posts VALUES (?,?,?,unixepoch())", (DID, key, position))
    client = client_for()
    items = client.get("/api/digest").json()["items"]
    assert len(items) == 10
    assert {item["key"] for item in items} <= set(keys)
    selected_key = items[0]["key"]
    response = client.get("/me")
    assert "Original post" in response.text
    assert profiles.head(conn, uid) is None
    response = client.post(
        "/me/feedback",
        data={"csrf": "csrf-test", "text": "less hype", "key": "first", "post_key": selected_key},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert conn.execute("SELECT post_key FROM feedback_jobs").fetchone()[0] == selected_key
    assert profiles.head(conn, uid) is None


def test_initial_feed_respects_membership_blocks_and_dismissals(conn):
    add_user(conn, DID)
    uid = profiles.bootstrap(conn, DID)
    post = insert_post(conn, "did:plc:human", "visible", "Visible post")
    blocked_post = insert_post(conn, "did:plc:blocked", "blocked", "Blocked post")
    dismissed_post = insert_post(conn, "did:plc:human", "dismissed", "Dismissed post")
    other_post = insert_post(conn, "did:plc:human", "other", "Other reader post")
    conn.execute("INSERT INTO bluesky_feed_sync VALUES (?,'feed','ready',unixepoch(),unixepoch(),3)", (DID,))
    for position, uri in enumerate((blocked_post, dismissed_post, post)):
        conn.execute("INSERT INTO bluesky_feed_posts VALUES (?,?,?,unixepoch())", (DID, "bluesky:" + uri, position))
    conn.execute(
        "INSERT INTO bluesky_feed_posts VALUES (?,?,0,unixepoch())", ("did:plc:other", "bluesky:" + other_post)
    )
    conn.execute("UPDATE reader_settings SET blocked_authors=? WHERE user_id=?", ('["bluesky:did:plc:blocked"]', uid))
    conn.execute("INSERT INTO dismissed_posts VALUES (?,?,unixepoch())", (uid, "bluesky:" + dismissed_post))
    assert {item["key"] for item in digests.read(conn, uid)} == {"bluesky:" + post, "bluesky:" + other_post}
