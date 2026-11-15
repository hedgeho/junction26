import base64
import json

import pytest
from curator_core import db
from curator_core.config import clear_settings
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


def test_landing_page_links_to_working_sign_in_form(conn):
    client = TestClient(create_app(), base_url="https://feed.example.com")
    response = client.get("/")
    assert response.status_code == 200
    assert response.text.count('href="#join"') >= 2
    assert 'id="join"' in response.text
    assert 'action="/login"' in response.text
    assert 'name="csrf"' in response.text
    assert 'value="bluesky"' in response.text
    assert 'value="threads" disabled' in response.text
    assert "Threads sign-in is not yet supported" in response.text
    assert "including deleting your posts" not in response.text
    assert "Resurrect the dead internet" in response.text


def seed(conn, source_text="Original human post about compilers", **post_cols):
    add_user(conn, DID, "GENERATED_CONFIG_SENTINEL")
    uid = profiles.bootstrap(conn, DID)
    uri = insert_post(conn, "did:plc:human", "1", source_text, **post_cols)
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


def test_processing_notice_is_hidden_when_feed_has_posts(conn, monkeypatch):
    from curator_curation import health

    seed(conn)
    monkeypatch.setattr(
        health, "filtering_status", lambda conn, uid: {"state": "processing", "label": "Filtering posts..."}
    )
    response = client_for().get("/me")
    assert '<div class="notice" role="status" hidden><span id="curation-pending">' in response.text
    assert "Original human post" in response.text


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


def test_profile_edit_takes_written_search_terms_and_keeps_unwritten_ones(conn):
    uid = seed(conn)
    client = client_for()
    revision = profiles.save(conn, uid, "public", "Goals: birds", profiles.head(conn, uid)["id"], terms=["birds"])
    body = "Goals: birds, volleyball\n\n- [search_term] volleyball\n- [search_term] Volleyball\n- [search_term] qubit\n"
    data = {"csrf": "csrf-test", "body": body, "revision": revision}
    assert client.post("/settings/profile/public", data=data, follow_redirects=False).status_code == 303
    head = profiles.head(conn, uid)
    assert json.loads(head["search_terms"]) == ["volleyball", "qubit"]
    data = {"csrf": "csrf-test", "body": "Goals: volleyball, junction", "revision": head["id"]}
    assert client.post("/settings/profile/public", data=data, follow_redirects=False).status_code == 303
    assert json.loads(profiles.head(conn, uid)["search_terms"]) == ["volleyball", "qubit"]


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
    assert "Service status" not in client.get("/settings").text
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
        assert f">{value}</textarea>" in response.text
        assert 'role="alert"' in response.text
        assert 'name="enabled" value="yes" data-toggle="notes-fields" checked' in response.text


def test_notes_connection_is_minimal_and_accepts_multiple_links(conn, monkeypatch):
    import hashlib

    from curator_curation import drive_notes, note_folders

    seed(conn)
    monkeypatch.setattr(
        drive_notes,
        "scan",
        lambda connection, user, root: ({"entry.md": (root, hashlib.sha256(root.encode()).hexdigest())}, {root}),
    )
    client = client_for()
    links = (
        "https://drive.google.com/drive/folders/firstfolder123\nhttps://drive.google.com/drive/folders/secondfolder123"
    )
    response = client.post("/settings/notes/folder", data={"csrf": "csrf-test", "enabled": "yes", "folder": links})
    assert response.status_code == 200
    assert links in response.text
    assert "https://context.dev/privacy" in response.text
    assert "Folder names are sent" not in response.text and "Local folders" not in response.text
    assert "Threads</h2>" in response.text and "Coming soon" in response.text

    def blocked_sync(*args):
        raise AssertionError("Page requests must not fetch notes")

    monkeypatch.setattr(note_folders, "sync", blocked_sync)
    assert client.get("/me").status_code == 200
    assert client.get("/settings").status_code == 200


def test_note_matching_distinguishes_missing_notes_provider_and_empty_results(conn, monkeypatch):
    from curator_core.config import clear_settings
    from curator_curation import notes

    uid = seed(conn)
    client = client_for()
    assert client.get("/api/note-matches").json()["state"] == "no_notes"
    notes.import_note(conn, uid, "Owned note", "Original thoughts about compilers")
    monkeypatch.setenv("GEMMA_URL", "")
    monkeypatch.setenv("GEMMA_TOKEN", "")
    clear_settings()
    assert client.get("/api/note-matches").json()["state"] == "unavailable"
    monkeypatch.setenv("GEMMA_URL", "https://gemma.example/v1")
    monkeypatch.setenv("GEMMA_TOKEN", "test")
    clear_settings()
    run = digests.latest(conn, uid)
    for state in ("processing", "retrying", "partial", "ready"):
        db.kv_set(
            conn,
            "note-matching:" + uid,
            json.dumps({"run_id": run["id"], "state": state, "completed": 2, "total": 3}),
        )
        result = client.get("/api/note-matches").json()
        assert result["state"] == state
        assert result["match_count"] == 0
        assert result["completed"] == 2 and result["total"] == 3
    db.kv_set(conn, "note-matching:" + uid, json.dumps({"run_id": "older-run", "state": "ready", "total": 3}))
    result = client.get("/api/note-matches").json()
    assert result["state"] == "waiting" and result["total"] == result["completed"] == 0


def test_note_matches_api_is_owned_incremental_and_source_only(conn, monkeypatch):
    from curator_core.config import clear_settings
    from curator_curation import notes
    from curator_curation.llm.schemas import NoteMatches, NotePostMatch

    uid = seed(conn)
    notes.import_note(conn, uid, "Original title", "Exact original note text")
    run = digests.latest(conn, uid)
    monkeypatch.setenv("GEMMA_URL", "https://gemma.example/v1")
    monkeypatch.setenv("GEMMA_TOKEN", "test")
    clear_settings()

    class Model:
        def match_note(self, posts, chunks, profile):
            return NoteMatches(
                items=[NotePostMatch(post_key=posts[0]["post_key"], chunk_id=chunks[0]["chunk_id"], relevance=0.9)]
            )

    client = client_for()
    before = client.get("/api/note-matches")
    assert before.json()["state"] == "waiting"
    assert before.headers["cache-control"] == "no-store"
    digests.match_notes(conn, uid, run["id"], Model())
    after = client.get("/api/note-matches").json()
    assert after["state"] == "ready" and after["run_id"] == run["id"]
    assert next(iter(after["items"].values()))[0]["text"] == "Exact original note text"
    assert after["match_count"] == 1 and after["completed"] == after["total"] == 1
    page = client.get("/me").text
    assert "From your notes &middot; Original title" in page
    assert 'class="use-note" disabled>Use as comment</button>' in page
    other = "did:plc:other-reader"
    add_user(conn, other)
    assert all(not matches for matches in client_for(other).get("/api/note-matches").json()["items"].values())


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
    for path in ("/static/app.css", "/static/app.js", "/static/theme.js", "/static/landing.css", "/static/landing.js"):
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


def test_post_details_only_hydrates_the_readers_displayed_posts(conn, monkeypatch):
    from curator_web import appview

    uid = seed(conn)
    visible = digests.read(conn, uid)[0]
    uri = visible["key"].removeprefix("bluesky:")
    requested = []

    def fake_views(uris):
        requested.extend(uris)
        return [
            {
                "uri": uri,
                "author": {
                    "did": "did:plc:human",
                    "handle": "human.bsky.social",
                    "displayName": "A person",
                    "avatar": "https://cdn.bsky.app/avatar.png",
                },
                "record": {"text": visible["text"]},
            },
            {"uri": "at://another/post/hidden", "author": {"displayName": "Hidden"}},
        ]

    monkeypatch.setattr(appview, "get_posts", fake_views)
    data = client_for().get("/api/post-details").json()["items"]
    assert requested == [uri]
    assert set(data) == {visible["key"]}
    assert data[visible["key"]]["author_name"] == "A person"
    assert data[visible["key"]]["author_handle"] == "human.bsky.social"


def test_imported_x_media_and_author_metadata_reach_the_reader(conn):
    from curator_core.imports import import_posts
    from curator_web.post_details import imported_details

    import_posts(
        conn,
        [
            {
                "platform": "x",
                "native_id": "123",
                "author_id": "writer",
                "author_name": "Writer",
                "author_handle": "writer",
                "author_avatar": "https://pbs.twimg.com/profile_images/1/a.jpg",
                "text": "Original",
                "canonical_url": "https://x.com/i/status/123",
                "media": [
                    {
                        "type": "image",
                        "thumb": "https://pbs.twimg.com/media/a.jpg",
                        "full": "https://pbs.twimg.com/media/a.jpg?name=large",
                        "alt": "A photo",
                    },
                    {"type": "link", "url": "https://example.org/a", "title": "Article"},
                ],
            }
        ],
    )
    add_user(conn, DID)
    uid = profiles.bootstrap(conn, DID)
    post = next(item for item in digests.read(conn, uid) if item["platform"] == "x")
    details = imported_details(post)
    assert details["author_handle"] == "writer"
    assert details["author_avatar"] == "https://pbs.twimg.com/profile_images/1/a.jpg"
    assert details["images"] == [
        {
            "url": "https://pbs.twimg.com/media/a.jpg",
            "full_url": "https://pbs.twimg.com/media/a.jpg?name=large",
            "alt": "A photo",
        }
    ]
    assert details["external"]["title"] == "Article"


def test_post_details_falls_back_to_ingested_bluesky_media(conn, monkeypatch):
    from curator_web import appview

    items = [{"type": "video", "thumb": "https://video.cdn.bsky.app/hls/did:plc:human/c/thumbnail.jpg", "alt": ""}]
    monkeypatch.setattr("curator_curation.media.fetch_images", lambda wanted: ())
    uid = seed(conn, media=json.dumps(items))
    key = digests.read(conn, uid)[0]["key"]
    monkeypatch.setattr(appview, "get_posts", lambda uris: [])
    details = client_for().get("/api/post-details").json()["items"][key]
    assert details["images"][0]["url"] == items[0]["thumb"]
    assert details["images"][0]["full_url"].startswith("https://bsky.app/profile/did:plc:human/post/")


def test_feed_daily_limit_dialog_and_update_preserve_other_settings(conn):
    uid = seed(conn)
    client = client_for()
    conn.execute(
        "UPDATE reader_settings SET timezone='Europe/Helsinki', blocked_authors='[\"x:blocked\"]' WHERE user_id=?",
        (uid,),
    )
    before = conn.execute("SELECT * FROM reader_settings WHERE user_id=?", (uid,)).fetchone()
    html = client.get("/me").text
    assert 'id="daily-limit-open"' in html
    assert 'id="daily-limit-dialog"' in html
    assert 'action="/me/refresh"' not in html
    assert "Target number of posts" not in html
    assert 'id="image-dialog"' in html
    assert 'name="daily_limit" min="1" max="50" value="10"' in html
    assert client.post("/me/daily-limit", data={"daily_limit": 3}).status_code == 403
    for value in (0, 51):
        assert client.post("/me/daily-limit", data={"csrf": "csrf-test", "daily_limit": value}).status_code == 400
    assert (
        client.post("/me/daily-limit", data={"csrf": "csrf-test", "daily_limit": 3}, follow_redirects=False).status_code
        == 303
    )
    after = conn.execute("SELECT * FROM reader_settings WHERE user_id=?", (uid,)).fetchone()
    assert after["daily_limit"] == 3 and after["refresh_requested"] == 1
    assert after["version"] == before["version"] + 1
    assert after["timezone"] == before["timezone"]
    assert after["blocked_authors"] == before["blocked_authors"]
    assert 'name="daily_limit" min="1" max="50" value="3"' in client.get("/me").text
    client.post("/me/daily-limit", data={"csrf": "csrf-test", "daily_limit": 3})
    assert conn.execute("SELECT version FROM reader_settings WHERE user_id=?", (uid,)).fetchone()[0] == after["version"]


def test_more_request_is_intentional_idempotent_and_resets_daily(conn, monkeypatch):
    from curator_core.config import clear_settings
    from curator_curation.llm.schemas import ProfileProposal

    monkeypatch.setenv("SCORING_BACKEND", "gemma")
    clear_settings()
    uid = seed(conn)
    for number in range(24):
        insert_post(conn, f"did:plc:author{number}", str(number), f"Original source post number {number}")

    class Scorer:
        def score(self, request):
            return Assessments(
                items=[
                    Assessment(post_key=p.post_key, decision="include", relevance=0.9, substance=0.8, opportunity=0.7)
                    for p in request.candidates
                ]
            )

    selected_keys = []

    class Gemma:
        def update_profile(self, current, feedback, terms=()):
            return ProfileProposal(markdown=current + "\n- [topic] Current: technical research\n", search_terms=[])

        def select_feed(self, posts, limit, profile, request):
            assert request and "More technical research" in request["instruction"]
            selected_keys[:] = [post["post_key"] for post in reversed(posts)][:limit]
            return selected_keys

    conn.execute("UPDATE reader_settings SET refresh_requested=1 WHERE user_id=?", (uid,))
    worker.run_cycle(conn, Scorer())
    assert len(digests.read(conn, uid)) == 10
    previous_run = digests.latest(conn, uid)["id"]
    client = client_for()
    page = client.get("/me").text
    assert "Shape your feed" in page
    assert "Remember for future picks" in page
    for action in ("Comment", "Like", "Save"):
        assert f'aria-label="{action}"' in page
    assert 'aria-label="Repost options"' in page
    assert 'data-action="repost"' in page
    assert 'data-action="quote"' in page
    assert 'aria-label="Share post"' in page
    assert "Shape your feed" in page
    assert 'id="feedback-panel"' in page
    assert 'id="feedback-dialog"' not in page
    assert 'name="kind"' not in page
    assert "Tell your feed" in page
    assert 'formaction="/me/more"' in page
    assert client.post("/me/more", data={"key": "more-one", "text": "More technical research"}).status_code == 403
    data = {"csrf": "csrf-test", "key": "more-one", "text": "More technical research"}
    assert client.post("/me/more", data=data, follow_redirects=False).status_code == 303
    assert client.post("/me/more", data=data, follow_redirects=False).status_code == 303
    config = digests.config_for(conn, uid)
    assert config["daily_limit"] == 10
    assert digests.delivery_limit(config) == 10
    assert conn.execute("SELECT count(*) FROM feedback_jobs WHERE user_id=?", (uid,)).fetchone()[0] == 1
    limits = []
    original_score_user = worker.score_user

    def record_score_limit(*args, **kwargs):
        limits.append(kwargs.get("limit"))
        return original_score_user(*args, **kwargs)

    monkeypatch.setattr(worker, "score_user", record_score_limit)
    worker.run_cycle(conn, Scorer(), Gemma())
    assert limits == [40]
    assert "technical research" in profiles.markdown(conn, uid)
    assert digests.latest(conn, uid)["id"] != previous_run
    assert selected_keys
    assert [post["key"] for post in digests.read(conn, uid)] == selected_keys
    assert not conn.execute("SELECT 1 FROM feed_requests WHERE user_id=?", (uid,)).fetchone()
    assert "More technical research" not in client.get("/me").text
    conn.execute("UPDATE reader_settings SET requested_more_day='2000-01-01' WHERE user_id=?", (uid,))
    assert digests.delivery_limit(digests.config_for(conn, uid)) == 10
    assert len(digests.read(conn, uid)) == 10


def test_social_actions_require_csrf_and_a_post_in_the_readers_feed(conn, monkeypatch):
    uid = seed(conn)
    post = digests.read(conn, uid)[0]
    calls = []

    async def interact(did, uri, action, text):
        calls.append((did, uri, action, text))
        return {"active": True}

    monkeypatch.setattr("curator_web.social.interact", interact)
    client = client_for()
    data = {"post_key": post["key"], "action": "comment", "text": "My own words"}
    assert client.post("/api/social-action", data=data).status_code == 403
    data["csrf"] = "csrf-test"
    assert client.post("/api/social-action", data=data).json() == {"active": True}
    assert calls == [(DID, post["key"].removeprefix("bluesky:"), "comment", "My own words")]
    data["post_key"] = "bluesky:at://did:plc:other/app.bsky.feed.post/not-in-feed"
    assert client.post("/api/social-action", data=data).status_code == 400
    assert len(calls) == 1


def test_contributions_are_restored_only_from_verified_bluesky_posts(conn, monkeypatch):
    uid = seed(conn)
    post_key = digests.read(conn, uid)[0]["key"]
    source = post_key.removeprefix("bluesky:")
    posted = f"at://{DID}/app.bsky.feed.post/myreply"

    async def interact(did, uri, action, text):
        return {"active": True, "record_uri": posted}

    async def states(did, uris):
        return {source: {"like": False, "repost": False, "save": False}}

    monkeypatch.setattr("curator_web.social.interact", interact)
    monkeypatch.setattr("curator_web.social.states", states)
    monkeypatch.setattr(
        "curator_web.appview.get_posts",
        lambda uris: [
            {
                "uri": posted,
                "author": {"did": DID},
                "record": {"text": "My reply", "reply": {"parent": {"uri": source}}},
            }
        ],
    )
    client = client_for()
    data = {"csrf": "csrf-test", "post_key": post_key, "action": "comment", "text": "My reply"}
    assert client.post("/api/social-action", data=data).json()["record_uri"] == posted
    assert client.get("/api/social-state").json()["contributions"][source] == [
        {"kind": "comment", "text": "My reply", "uri": posted}
    ]
    monkeypatch.setattr("curator_web.appview.get_posts", lambda uris: [])
    assert client.get("/api/social-state").json()["contributions"] == {}


def test_comments_are_loaded_for_feed_posts_only(conn, monkeypatch):
    uid = seed(conn)
    post_key = digests.read(conn, uid)[0]["key"]
    monkeypatch.setattr(
        "curator_web.appview.get_thread",
        lambda value: {
            "post": {"uri": value, "replyCount": 3},
            "replies": [
                {
                    "post": {
                        "uri": "at://did:plc:other/app.bsky.feed.post/reply",
                        "author": {"did": "did:plc:other", "handle": "other.example", "displayName": "Other"},
                        "record": {"text": "Hello!"},
                    }
                },
                {"$type": "app.bsky.feed.defs#blockedPost"},
            ],
        },
    )
    client = client_for()
    result = client.get("/api/comments", params={"post_key": post_key})
    assert result.status_code == 200
    assert result.json()["total"] == 3
    assert result.json()["items"][0]["text"] == "Hello!"
    assert result.json()["items"][0]["author"] == "Other"
    assert len(result.json()["items"]) == 1
    assert client.get("/api/comments", params={"post_key": "bluesky:at://unknown"}).status_code == 404
    html = client.get("/me").text
    assert "Show comments" in html
    assert 'id="feedback-original"' in html


def test_delete_comment_requires_csrf_and_feed_membership(conn, monkeypatch):
    uid = seed(conn)
    post_key = digests.read(conn, uid)[0]["key"]
    record_uri = f"at://{DID}/app.bsky.feed.post/reply"
    calls = []

    async def delete(did, source, uri):
        calls.append((did, source, uri))

    monkeypatch.setattr("curator_web.social.delete_comment", delete)
    client = client_for()
    data = {"post_key": post_key, "comment_uri": record_uri}
    assert client.post("/api/comment/delete", data=data).status_code == 403
    data["csrf"] = "csrf-test"
    data["post_key"] = "bluesky:at://another/post"
    assert client.post("/api/comment/delete", data=data).status_code == 404
    assert not calls
    data["post_key"] = post_key
    assert client.post("/api/comment/delete", data=data).json() == {"deleted": True}
    assert calls == [(DID, post_key.removeprefix("bluesky:"), record_uri)]


def test_nested_comments_require_ancestor_in_feed(conn, monkeypatch):
    uid = seed(conn)
    post_key = digests.read(conn, uid)[0]["key"]
    root = post_key.removeprefix("bluesky:")
    branch = "at://did:plc:other/app.bsky.feed.post/branch"
    leaf = "at://did:plc:other/app.bsky.feed.post/leaf"
    parent = {"post": {"uri": root}}

    def get_thread(uri):
        return {
            "post": {"uri": uri, "replyCount": 1},
            "parent": parent,
            "replies": [
                {
                    "post": {
                        "uri": leaf,
                        "author": {"did": "did:plc:other"},
                        "record": {"text": "A nested reply"},
                        "replyCount": 2,
                    }
                }
            ],
        }

    monkeypatch.setattr("curator_web.appview.get_thread", get_thread)
    client = client_for()
    result = client.get("/api/comments", params={"post_key": post_key, "parent_uri": branch})
    assert result.status_code == 200
    assert result.json()["items"][0]["text"] == "A nested reply"
    assert result.json()["items"][0]["reply_count"] == 2
    parent["post"]["uri"] = "at://did:plc:unrelated/app.bsky.feed.post/other"
    assert client.get("/api/comments", params={"post_key": post_key, "parent_uri": branch}).status_code == 502


def test_comment_count_is_in_hydrated_post_details(conn, monkeypatch):
    uid = seed(conn)
    post_key = digests.read(conn, uid)[0]["key"]
    uri = post_key.removeprefix("bluesky:")
    monkeypatch.setattr(
        "curator_web.appview.get_posts", lambda uris: [{"uri": uri, "replyCount": 0, "author": {}, "record": {}}]
    )
    assert client_for().get("/api/post-details").json()["items"][post_key]["reply_count"] == 0
    html = client_for().get("/me").text
    assert 'class="comments-toggle" aria-expanded="false" hidden' in html


def test_deleting_nested_comment_checks_the_branch(conn, monkeypatch):
    uid = seed(conn)
    post_key = digests.read(conn, uid)[0]["key"]
    root = post_key.removeprefix("bluesky:")
    branch = "at://did:plc:other/app.bsky.feed.post/branch"
    comment = f"at://{DID}/app.bsky.feed.post/reply"
    ancestor = {"post": {"uri": root}}
    monkeypatch.setattr("curator_web.appview.get_thread", lambda uri: {"post": {"uri": uri}, "parent": ancestor})
    calls = []

    async def delete(did, source, uri):
        calls.append((did, source, uri))

    monkeypatch.setattr("curator_web.social.delete_comment", delete)
    client = client_for()
    data = {"csrf": "csrf-test", "post_key": post_key, "parent_uri": branch, "comment_uri": comment}
    assert client.post("/api/comment/delete", data=data).status_code == 200
    assert calls == [(DID, branch, comment)]
    ancestor["post"]["uri"] = "at://did:plc:other/app.bsky.feed.post/unrelated"
    assert client.post("/api/comment/delete", data=data).status_code == 400
    assert len(calls) == 1


def test_feedback_panel_explains_the_curation_pipeline(conn):
    seed(conn)
    html = client_for().get("/me").text
    assert 'id="algorithm-info-open"' in html
    assert 'id="algorithm-info-dialog"' in html
    assert 'aria-controls="algorithm-info-dialog"' in html
    assert "ABOUT THIS POST" not in html
    assert 'id="feedback-context"' not in html
    assert 'class="feedback-submit"' in html
    assert "Private notes are not used for discovery" in html
    assert "your notes are not sent to Gemini" in html
    assert 'name="post_key" id="feedback-post"' in html


def test_reply_actions_are_scoped_to_the_displayed_thread(conn, monkeypatch):
    uid = seed(conn)
    post_key = digests.read(conn, uid)[0]["key"]
    root = post_key.removeprefix("bluesky:")
    reply = "at://did:plc:other/app.bsky.feed.post/reply"
    ancestor = {"post": {"uri": root}}
    monkeypatch.setattr("curator_web.appview.get_thread", lambda uri: {"post": {"uri": uri}, "parent": ancestor})
    calls = []

    async def interact(did, uri, action, text):
        calls.append((did, uri, action, text))
        return {"active": True, "record_uri": f"at://{did}/app.bsky.feed.post/new"}

    monkeypatch.setattr("curator_web.social.interact", interact)
    client = client_for()
    data = {"csrf": "csrf-test", "post_key": post_key, "target_uri": reply, "action": "comment", "text": "My response"}
    assert client.post("/api/social-action", data=data).status_code == 200
    assert calls == [(DID, reply, "comment", "My response")]
    assert conn.execute("SELECT source_uri FROM social_contributions WHERE user_did=?", (DID,)).fetchone()[0] == reply
    ancestor["post"]["uri"] = "at://did:plc:other/app.bsky.feed.post/unrelated"
    assert client.post("/api/social-action", data=data).status_code == 400
    assert len(calls) == 1
    data.pop("csrf")
    assert client.post("/api/social-action", data=data).status_code == 403


def test_comment_interaction_states_are_optional(conn, monkeypatch):
    uid = seed(conn)
    post_key = digests.read(conn, uid)[0]["key"]
    reply = "at://did:plc:other/app.bsky.feed.post/reply"
    monkeypatch.setattr(
        "curator_web.appview.get_thread",
        lambda uri: {
            "post": {"uri": uri},
            "replies": [{"post": {"uri": reply, "author": {}, "record": {"text": "Hi"}}}],
        },
    )

    async def states(did, uris):
        assert uris == [reply]
        return {reply: {"like": True, "repost": False, "save": True}}

    monkeypatch.setattr("curator_web.social.states", states)
    client = client_for()
    item = client.get("/api/comments", params={"post_key": post_key}).json()["items"][0]
    assert item["state"] == {"like": True, "repost": False, "save": True}

    async def unavailable(did, uris):
        raise RuntimeError("offline")

    monkeypatch.setattr("curator_web.social.states", unavailable)
    item = client.get("/api/comments", params={"post_key": post_key}).json()["items"][0]
    assert item["text"] == "Hi" and item["state"] is None


def test_settings_configuration_files_use_purpose_based_names(conn):
    uid = seed(conn)
    profiles.save(conn, uid, "private", "# Notes interests", None)
    client = client_for()
    page = client.get("/settings").text
    assert "Feed curation" in page and "Note matching" in page
    assert "selected for your feed" in page and "matched to curated posts" in page
    assert "Service status" not in page and "<summary>Delivery</summary>" not in page
    assert "/settings/profile/feed-curation" in page and "/settings/profile/note-matching" in page
    for slug, kind in (("feed-curation", "public"), ("note-matching", "private")):
        response = client.get(f"/settings/profile/{slug}")
        assert response.status_code == 200
        assert f'filename="{slug}.md"' in response.headers["content-disposition"]
        revision = profiles.head(conn, uid, kind)["id"]
        response = client.post(
            f"/settings/profile/{slug}",
            data={"csrf": "csrf-test", "body": f"# Updated {slug}", "revision": revision},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert profiles.markdown(conn, uid, kind) == f"# Updated {slug}"


def test_delete_quote_on_reply_removes_only_its_stored_reference(conn, monkeypatch):
    uid = seed(conn)
    post_key = digests.read(conn, uid)[0]["key"]
    root = post_key.removeprefix("bluesky:")
    reply = "at://did:plc:other/app.bsky.feed.post/reply"
    quote = f"at://{DID}/app.bsky.feed.post/quote"
    conn.execute("INSERT INTO social_contributions VALUES (?,?,?,?,unixepoch())", (DID, reply, quote, "quote"))
    monkeypatch.setattr(
        "curator_web.appview.get_thread", lambda uri: {"post": {"uri": uri}, "parent": {"post": {"uri": root}}}
    )
    calls = []

    async def delete(did, source, uri):
        calls.append((did, source, uri))

    monkeypatch.setattr("curator_web.social.delete_quote", delete)
    data = {"csrf": "csrf-test", "post_key": post_key, "parent_uri": reply, "comment_uri": quote, "kind": "quote"}
    assert client_for().post("/api/comment/delete", data=data).json() == {"deleted": True}
    assert calls == [(DID, reply, quote)]
    assert not conn.execute("SELECT 1 FROM social_contributions WHERE record_uri=?", (quote,)).fetchone()


def test_threads_blocks_are_accepted_and_unknown_platforms_rejected(conn):
    seed(conn)
    client = client_for()
    form = {"csrf": "csrf-test", "daily_limit": 5, "timezone": "UTC"}
    ok = client.post("/settings/delivery", data=form | {"blocked_authors": "threads:alice"}, follow_redirects=False)
    assert ok.status_code == 303
    assert client.post("/settings/delivery", data=form | {"blocked_authors": "mastodon:bob"}).status_code == 400


def test_imported_threads_media_reaches_the_reader(conn):
    from curator_core.imports import import_posts
    from curator_web.post_details import imported_details

    cdn = "https://scontent-iad3-1.cdninstagram.com/v/a.jpg"
    import_posts(
        conn,
        [
            {
                "platform": "threads",
                "native_id": "1790",
                "author_id": "alice",
                "author_handle": "alice",
                "text": "Original",
                "canonical_url": "https://www.threads.com/@alice/post/abc",
                "media": [{"type": "image", "thumb": cdn, "full": cdn, "alt": "A photo"}],
            }
        ],
    )
    add_user(conn, DID)
    uid = profiles.bootstrap(conn, DID)
    post = next(item for item in digests.read(conn, uid) if item["platform"] == "threads")
    assert imported_details(post)["images"] == [{"url": cdn, "full_url": cdn, "alt": "A photo"}]


def test_feedback_ajax_returns_owned_job_and_fresh_key(conn):
    uid = seed(conn)
    post_key = digests.read(conn, uid)[0]["key"]
    client = client_for()
    response = client.post(
        "/me/feedback",
        data={"csrf": "csrf-test", "text": "More like this", "key": "ajax-one", "post_key": post_key},
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 202
    payload = response.json()
    assert payload["next_key"] != "ajax-one"
    assert client.get("/api/jobs/" + payload["job_id"]).json() == {"state": "queued"}
    row = conn.execute("SELECT post_context FROM feedback_jobs WHERE id=?", (payload["job_id"],)).fetchone()
    assert "Original human post" in row["post_context"]


def test_theme_toggle_has_an_icon_not_a_placeholder(conn):
    html = TestClient(create_app(), base_url="https://feed.example.com").get("/").text
    assert 'aria-hidden="true">?<' not in html
    assert 'aria-hidden="true">&#9680;<' in html


def test_funnel_api_returns_only_counts_for_the_signed_in_reader(conn):
    seed(conn)
    response = client_for().get("/api/funnel")
    assert response.status_code == 200
    body = response.json()
    assert body["stages"][-1] == {"key": "delivered", "label": "In today's feed", "count": 1, "drops": []}
    assert "compilers" not in response.text.casefold()
    assert (
        TestClient(create_app(), base_url="https://feed.example.com", follow_redirects=False)
        .get("/api/funnel")
        .status_code
        == 303
    )


def test_feed_request_uses_visible_context_is_one_shot_and_respects_cap(conn, monkeypatch):
    from curator_core.config import clear_settings
    from curator_curation.llm.schemas import ProfileProposal

    monkeypatch.setenv("SCORING_BACKEND", "gemma")
    clear_settings()
    uid = seed(conn)
    conn.execute("UPDATE reader_settings SET daily_limit=2 WHERE user_id=?", (uid,))
    old_keys = {post["key"] for post in digests.read(conn, uid)}
    for index in range(6):
        insert_post(conn, f"did:plc:fresh{index}", str(index), f"Fresh technical post {index}")

    class Scorer:
        def __init__(self):
            self.prompts = []

        def score(self, request):
            self.prompts.append(request.explicit_profile)
            return Assessments(
                items=[
                    Assessment(post_key=p.post_key, decision="include", relevance=0.9, substance=0.8, opportunity=0.7)
                    for p in request.candidates
                ]
            )

    class Model:
        def update_profile(self, current, feedback, terms=()):
            return ProfileProposal(markdown=current + "\n- [topic] Tentative: technical posts\n", search_terms=[])

        def select_feed(self, posts, limit, profile, request):
            assert limit == 2
            assert request["instruction"] == "more like this"
            assert "Original human post about compilers" in request["visible_posts"]
            return [post["post_key"] for post in posts]  # Deliberately exceed the limit.

        def match(self, post, chunks, profile):
            raise AssertionError("No notes in this test")

    client = client_for()
    response = client.post("/me/more", data={"csrf": "csrf-test", "key": "once", "text": "more like this"})
    assert response.status_code == 200
    scorer = Scorer()
    worker.run_cycle(conn, scorer, Model())
    assert any(
        "more like this" in prompt and "Original human post about compilers" in prompt for prompt in scorer.prompts
    )
    posts = digests.read(conn, uid)
    assert len(posts) == 2
    assert not old_keys.intersection(post["key"] for post in posts)
    assert not conn.execute("SELECT 1 FROM feed_requests WHERE user_id=?", (uid,)).fetchone()
    job = conn.execute("SELECT feedback, feed_context FROM feedback_jobs WHERE idempotency_key='once'").fetchone()
    assert tuple(job) == ("", None)


def test_failed_final_selection_preserves_feed_and_request(conn, monkeypatch):
    from curator_core.config import clear_settings
    from curator_curation.llm.schemas import ProfileProposal

    monkeypatch.setenv("SCORING_BACKEND", "gemma")
    clear_settings()
    uid = seed(conn)
    before = digests.latest(conn, uid)["id"]
    insert_post(conn, "did:plc:fresh", "fresh", "A fresh technical post")

    class Scorer:
        def score(self, request):
            return Assessments(
                items=[
                    Assessment(post_key=p.post_key, decision="include", relevance=0.9, substance=0.8, opportunity=0.7)
                    for p in request.candidates
                ]
            )

    class Model:
        def update_profile(self, current, feedback, terms=()):
            return ProfileProposal(markdown=current, search_terms=[])

        def select_feed(self, posts, limit, profile, request):
            return ["unknown-post"]

    response = client_for().post("/me/more", data={"csrf": "csrf-test", "key": "retry", "text": "technical"})
    assert response.status_code == 200
    worker.run_cycle(conn, Scorer(), Model())
    assert digests.latest(conn, uid)["id"] == before
    assert conn.execute("SELECT 1 FROM feed_requests WHERE user_id=?", (uid,)).fetchone()
    assert db.kv_get(conn, "curation-state:" + uid) == "failed"


def test_preference_feedback_keeps_current_selection_and_reports_changes(conn):
    from curator_curation import jobs
    from curator_curation.llm.schemas import ProfileProposal

    uid = seed(conn)
    before = digests.latest(conn, uid)["id"]
    conn.execute("UPDATE reader_settings SET refresh_requested=0 WHERE user_id=?", (uid,))

    class Model:
        def update_profile(self, current, feedback, terms=()):
            return ProfileProposal(markdown=current + "\n- [topic] More research\n", search_terms=[])

    client = client_for()
    result = client.post(
        "/me/feedback",
        data={"csrf": "csrf-test", "key": "remember", "text": "more research"},
        headers={"Accept": "application/json"},
    ).json()
    assert jobs.process_one(conn, Model())
    assert digests.config_for(conn, uid)["refresh_requested"] == 0
    assert digests.latest(conn, uid)["id"] == before
    assert any("More research" in item for item in client.get("/api/jobs/" + result["job_id"]).json()["changes"])


def test_stale_visible_feed_cannot_be_used_as_context(conn):
    uid = seed(conn)
    client = client_for()
    for endpoint in ("/me/feedback", "/me/more"):
        response = client.post(
            endpoint,
            data={
                "csrf": "csrf-test",
                "key": endpoint,
                "text": "more like this",
                "visible_post_key": "bluesky:not-visible",
            },
        )
        assert response.status_code == 409
    assert not conn.execute("SELECT 1 FROM feedback_jobs WHERE user_id=?", (uid,)).fetchone()


def test_version_history_explains_literal_changes(conn):
    uid = seed(conn)
    before = profiles.head(conn, uid)["id"]
    revised = profiles.save(conn, uid, "public", "# Profile\n\n- [topic] More design research\n", before)
    changes = profiles.changes(conn, uid, revised)
    assert any("Added:" in change and "More design research" in change for change in changes)
    page = client_for().get("/settings").text
    assert "Previous versions" in page
    assert "More design research" in page
    assert "Restore this version" in page


def test_feed_embeds_imported_metadata_before_remote_hydration(conn):
    import re

    uid = seed(
        conn,
        media=json.dumps(
            [
                {
                    "type": "image",
                    "thumb": "https://cdn.bsky.app/thumb.png",
                    "full": "https://cdn.bsky.app/full.png",
                    "alt": "An original image",
                }
            ]
        ),
    )
    post = digests.read(conn, uid)[0]
    html = client_for().get("/me").text
    metadata = json.loads(
        re.search(r'<script id="post-metadata" type="application/json">(.*?)</script>', html).group(1)
    )
    assert list(metadata) == [post["key"]]
    assert metadata[post["key"]]["author_name"] == post["author"]
    assert metadata[post["key"]]["images"][0]["alt"] == "An original image"
    assert "/settings#services" not in html


def test_source_metadata_cannot_break_out_of_json_script(conn, monkeypatch):
    import re

    uid = seed(conn)
    posts = digests.read(conn, uid)
    posts[0]["author_handle"] = '</script><script>alert("source")</script>'
    monkeypatch.setattr("curator_web.site.reader_posts", lambda *_: posts)
    html = client_for().get("/me").text
    encoded = re.search(r'<script id="post-metadata" type="application/json">(.*?)</script>', html).group(1)
    assert "</script>" not in encoded
    assert next(iter(json.loads(encoded).values()))["author_handle"] == '</script><script>alert("source")</script>'


def test_configuration_history_retains_both_kinds_independently(conn):
    uid = seed(conn)
    old_private = profiles.save(conn, uid, "private", "# Notes\n- [topic] Earlier note interests", None)
    profiles.save(conn, uid, "private", "# Notes\n- [topic] Current note interests", old_private)
    for number in range(25):
        current = profiles.head(conn, uid)
        profiles.save(conn, uid, "public", f"# Feed preference revision {number}", current["id"] if current else None)
    html = client_for().get("/settings").text
    assert f"/settings/restore/{old_private}" in html
    assert "Earlier note interests" in html


def test_more_request_uses_gemma_alongside_normal_decision_scoring(conn):
    from curator_curation.config import settings as curation_settings
    from curator_curation.llm.schemas import ProfileProposal

    uid = seed(conn)
    old_keys = {post["key"] for post in digests.read(conn, uid)}
    for number in range(2):
        insert_post(conn, f"did:plc:fresh{number}", str(number), f"Original compiler implementation research {number}")
    calls = []

    class NormalScorer:
        def score(self, request):
            raise AssertionError("One-time instructions must not go to the public scorer")

    class Gemma:
        def update_profile(self, current, feedback, terms=()):
            return ProfileProposal(markdown=current, search_terms=[])

        def score(self, request):
            calls.append(request)
            return Assessments(
                items=[
                    Assessment(
                        post_key=post.post_key, decision="include", relevance=0.9, substance=0.8, opportunity=0.7
                    )
                    for post in request.candidates
                ]
            )

        def select_feed(self, posts, limit, profile, request):
            assert "Only compiler research now" in request["instruction"]
            return [post["post_key"] for post in posts][:limit]

    assert curation_settings().scoring_backend == "decisions"
    before = digests.latest(conn, uid)["id"]
    client_for().post(
        "/me/more", data={"csrf": "csrf-test", "key": "private-request", "text": "Only compiler research now"}
    )
    worker.run_cycle(conn, NormalScorer(), Gemma())
    assert calls and "Only compiler research now" in calls[0].explicit_profile
    assert digests.latest(conn, uid)["id"] != before
    assert len(digests.read(conn, uid)) == 2
    assert not old_keys.intersection(post["key"] for post in digests.read(conn, uid))
    versions = {
        row[0]
        for row in conn.execute("SELECT scorer_version FROM assessments_v2 WHERE scorer_version LIKE '%:request:%'")
    }
    assert versions and all(version.startswith("gemma-v1:") for version in versions)
    assert not conn.execute("SELECT 1 FROM feed_requests WHERE user_id=?", (uid,)).fetchone()
    assert curation_settings().scoring_backend == "decisions"


def test_restore_profile_keeps_original_unicode_and_line_endings(conn):
    uid = seed(conn)
    original = "# Interests\r\n\r\n- [topic] Compiler research and \u03bb calculus\r\n"
    first = profiles.save(conn, uid, "public", original, profiles.head(conn, uid)["id"])
    second = profiles.save(conn, uid, "public", "# Changed\n- [topic] Other interests\n", first)
    result = client_for().post(
        "/settings/restore/" + first,
        data={"csrf": "csrf-test", "expected": second},
        follow_redirects=False,
    )
    assert result.status_code == 303 and result.headers["location"] == "/settings#configuration"
    assert profiles.markdown(conn, uid) == original


def test_empty_compiled_profile_downloads_and_restores_without_reseeding(conn):
    uid = seed(conn)
    original = profiles.head(conn, uid)["id"]
    empty = profiles.save(conn, uid, "public", "", original, terms=[])
    client = client_for()
    assert client.get("/me").status_code == 200
    assert client.get("/settings").status_code == 200
    assert client.get("/settings/profile/public").text == ""
    restored = client.post(
        "/settings/restore/" + original,
        data={"csrf": "csrf-test", "expected": empty},
        follow_redirects=False,
    )
    assert restored.status_code == 303
    assert profiles.markdown(conn, uid)
    restored_empty = client.post(
        "/settings/restore/" + empty,
        data={"csrf": "csrf-test", "expected": profiles.head(conn, uid)["id"]},
        follow_redirects=False,
    )
    assert restored_empty.status_code == 303
    assert profiles.markdown(conn, uid) == ""


def test_feedback_result_reports_its_exact_revision_and_survives_feed_reload(conn):
    import re

    from curator_curation import jobs
    from curator_curation.llm.schemas import ProfileProposal

    uid = seed(conn)
    before = profiles.head(conn, uid)["id"]
    client = client_for()
    text = "More compiler research </script><script>window.pwned=1</script>"
    result = client.post(
        "/me/feedback",
        data={"csrf": "csrf-test", "key": "diff-job", "text": text},
        headers={"Accept": "application/json"},
    ).json()

    class Model:
        def update_profile(self, current, feedback, terms=()):
            return ProfileProposal(
                markdown=current + "\n" + "".join(f"- [topic] Research {i}\n" for i in range(7)), search_terms=[]
            )

    assert jobs.process_one(conn, Model())
    receipt = client.get("/api/jobs/" + result["job_id"]).json()
    diff = receipt["diff"]
    assert diff["parent_revision"] == before and diff["changed"]
    assert diff["added"] == 7 and receipt["feedback"] == text and receipt["post_key"] == ""
    assert any(line["text"] == "+- [topic] Research 6" for line in diff["lines"])
    profiles.save(conn, uid, "public", "# A later independent change\n", diff["revision"])
    assert client.get("/api/jobs/" + result["job_id"]).json()["diff"] == diff
    html = client.get("/me").text
    raw = re.search(r'<script id="feedback-receipts" type="application/json">(.*?)</script>', html).group(1)
    assert "</script>" not in raw and json.loads(raw)[0] == receipt
    assert "window.pwned=1</script>" not in client.get("/settings").text


@pytest.mark.parametrize("scope", ["post", "feed", "more"])
def test_feedback_model_gets_source_context_and_saves_only_principles(conn, monkeypatch, scope):
    from curator_curation import jobs
    from curator_curation.llm.gemma import GemmaClient
    from curator_curation.llm.schemas import PreferenceLine, ProfileEdits

    source_text = "Compiler evaluation " + "detailed evidence " * 40 + "Reproducible benchmark methodology"
    uid = seed(conn, source_text=source_text)
    post_key = digests.read(conn, uid)[0]["key"]
    seen = {}
    principle = "Prefer in-depth compiler evaluations with reproducible benchmarks"

    def call(self, instruction, payload, schema, max_tokens=8192):
        seen.update(instruction=instruction, payload=payload)
        return ProfileEdits(add=[PreferenceLine(category="topic", text=principle)])

    monkeypatch.setattr(GemmaClient, "_call", call)
    data = {"csrf": "csrf-test", "key": "context-" + scope, "text": "I want more of this"}
    if scope == "post":
        data["post_key"] = post_key
    else:
        data["visible_post_key"] = post_key
    client = client_for()
    response = client.post(
        "/me/more" if scope == "more" else "/me/feedback",
        data=data,
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 202
    assert jobs.process_one(conn, GemmaClient("https://gemma.example/v1", "test", "test"))
    prompt = seen["payload"]["feedback"]
    assert source_text in prompt and "Reproducible benchmark methodology" in prompt
    assert "I want more of this" in prompt
    assert "standalone curation principle" in seen["instruction"]
    assert "Never save the feedback verbatim" in prompt
    profile = profiles.markdown(conn, uid)
    assert principle in profile and "I want more of this" not in profile
    receipt = client.get("/api/jobs/" + response.json()["job_id"]).json()
    assert receipt["diff"]["changed"]
    assert not any(line["type"] == "context" for line in receipt["diff"]["lines"])


def test_noop_feedback_does_not_claim_the_previous_revision_diff(conn):
    from curator_curation import jobs

    uid = seed(conn)
    revision = profiles.save(
        conn, uid, "public", "# Existing preferences\n- [topic] Compilers\n", profiles.head(conn, uid)["id"]
    )
    job_id = jobs.enqueue(conn, uid, "public", "Only compilers now", "noop-diff", mode="more")
    conn.execute("UPDATE feedback_jobs SET state='applied',applied_revision=? WHERE id=?", (revision, job_id))
    receipt = client_for().get("/api/jobs/" + job_id).json()
    assert receipt["changes"] == [] and not receipt["diff"]["changed"] and receipt["diff"]["lines"] == []


@pytest.mark.parametrize("endpoint", ["/me/feedback", "/me/more"])
def test_full_feed_feedback_accepts_all_fifty_visible_posts(conn, monkeypatch, endpoint):
    from curator_web import site

    uid = seed(conn)
    source = digests.read(conn, uid)[0]
    posts = [dict(source, key=f"bluesky:at://did:plc:author/app.bsky.feed.post/{number}") for number in range(50)]
    monkeypatch.setattr(site.digests, "read", lambda *args: posts)
    fields = [("csrf", (None, "csrf-test")), ("key", (None, "fifty")), ("text", (None, "more research"))]
    fields.extend(("visible_post_key", (None, post["key"])) for post in posts)
    response = client_for().post(endpoint, files=fields, headers={"Accept": "application/json"})
    assert response.status_code == 202
    row = conn.execute("SELECT feed_context FROM feedback_jobs WHERE user_id=?", (uid,)).fetchone()
    assert len(json.loads(row["feed_context"])) == 50


def test_large_feed_context_has_a_shared_budget_and_preserves_post_endings(conn, monkeypatch):
    from curator_web import site

    uid = seed(conn)
    source = digests.read(conn, uid)[0]
    posts = [
        dict(source, key=f"bluesky:missing-{number}", text="Beginning " + "evidence " * 1000 + "Conclusion")
        for number in range(50)
    ]
    monkeypatch.setattr(site.digests, "read", lambda *args: posts)
    context = site.feed_context(conn, uid, [post["key"] for post in posts])
    snapshots = json.loads(context)
    assert len(snapshots) == 50 and len(context) < 30000
    assert all(post["text"].startswith("Beginning") and post["text"].endswith("Conclusion") for post in snapshots)
    assert all("[truncated]" in post["text"] for post in snapshots)


def test_feedback_diffs_are_scoped_to_each_post_and_each_document(conn):
    import re

    from curator_curation import jobs

    uid = seed(conn)
    key = digests.read(conn, uid)[0]["key"]
    for kind, post_key in (("public", key), ("private", None)):
        job_id = jobs.enqueue(conn, uid, kind, "Remember " + kind, "diff-" + kind, post_key)
        head = profiles.head(conn, uid, kind)
        revision = profiles.save(
            conn,
            uid,
            kind,
            "# New " + kind + " preferences\n",
            head["id"] if head else None,
            origin="feedback:" + job_id,
        )
        conn.execute("UPDATE feedback_jobs SET state='applied',applied_revision=? WHERE id=?", (revision, job_id))
    html = client_for().get("/me").text
    receipts = json.loads(
        re.search(r'<script id="feedback-receipts" type="application/json">(.*?)</script>', html).group(1)
    )
    assert {(receipt["post_key"], receipt["diff"]["filename"]) for receipt in receipts} == {
        (key, "feed-curation.md"),
        ("", "note-matching.md"),
    }


def test_configuration_diff_history_paginates_without_gaps_or_other_readers(conn):
    import re

    uid = seed(conn)
    parent = profiles.head(conn, uid)["id"]
    revisions = [parent]
    for number in range(23):
        parent = profiles.save(conn, uid, "public", f"# Revision {number}\n", parent)
        revisions.append(parent)
    other = profiles.reader_id(conn, "did:plc:someone-else")
    foreign = profiles.save(conn, other, "public", "# Other reader secret\n", None)
    client = client_for()
    html = client.get("/settings/history/feed-curation").text
    first = re.findall(r'data-revision="([a-f0-9]+)"', html)
    assert first == list(reversed(revisions))[:20] and foreign not in first
    assert "diff-code" in html and "View diff" in html
    cursor = re.search(r'href="/settings/history/feed-curation\?before=(\d+)"', html).group(1)
    older = client.get("/settings/history/feed-curation?before=" + cursor).text
    second = re.findall(r'data-revision="([a-f0-9]+)"', older)
    assert first + second == list(reversed(revisions))
    assert "history-older" not in older
    assert client.get("/settings/history/unknown").status_code == 404
    assert client.get("/settings/history/feed-curation?before=" + str(2**64)).status_code == 404
    assert "Other reader secret" not in html + older


def test_demo_is_disabled_without_a_configured_reader(conn, monkeypatch):
    seed(conn)
    monkeypatch.setenv("DEMO_DID", "")
    client = TestClient(create_app(), base_url="https://feed.example.com")
    assert client.get("/demo").status_code == 404
    assert client.get("/api/demo/post-details").status_code == 404
    assert 'href="/demo"' not in client.get("/").text
    monkeypatch.setenv("DEMO_DID", "did:plc:never-signed-in")
    clear_settings()
    assert client.get("/demo").status_code == 404
    assert conn.execute("SELECT 1 FROM users WHERE did='did:plc:never-signed-in'").fetchone() is None


def test_demo_shows_configured_feed_read_only_without_notes(conn, monkeypatch):
    from curator_web import appview

    uid = seed(conn)
    post = digests.read(conn, uid)[0]
    monkeypatch.setattr(
        digests,
        "read",
        lambda c, u: [dict(post, notes=[{"chunk_id": 1, "title": "Diary", "text": "PRIVATE_NOTE_SENTINEL"}])],
    )
    monkeypatch.setattr(appview, "get_posts", lambda uris: [])
    monkeypatch.setenv("DEMO_DID", DID)
    clear_settings()
    client = TestClient(create_app(), base_url="https://feed.example.com")
    landing = client.get("/").text
    assert 'class="cta cta-secondary" href="/demo"' in landing
    response = client.get("/demo")
    assert response.status_code == 200
    assert "Original human post" in response.text
    assert "data-read-only" in response.text
    assert 'data-funnel-url="/api/demo/funnel"' in response.text
    assert "PRIVATE_NOTE_SENTINEL" not in response.text
    assert "GENERATED_CONFIG_SENTINEL" not in response.text
    for action in ("/me/feedback", "/me/more", "/me/daily-limit", "social-interact"):
        assert action not in response.text
    details = client.get("/api/demo/post-details").json()["items"]
    assert set(details) == {post["key"]}
    assert "stages" in client.get("/api/demo/funnel").json()
    assert client.get("/me").url.path == "/"
