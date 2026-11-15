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


def seed(conn, **post_cols):
    add_user(conn, DID, "GENERATED_CONFIG_SENTINEL")
    uid = profiles.bootstrap(conn, DID)
    uri = insert_post(conn, "did:plc:human", "1", "Original human post about compilers", **post_cols)
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


def test_more_request_is_intentional_idempotent_and_resets_daily(conn):
    from curator_curation.llm.schemas import ProfileProposal

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

    class Gemma:
        def update_profile(self, current, feedback, terms=()):
            return ProfileProposal(markdown=current + "\n" + feedback, search_terms=[])

    conn.execute("UPDATE reader_settings SET refresh_requested=1 WHERE user_id=?", (uid,))
    worker.run_cycle(conn, Scorer())
    assert len(digests.read(conn, uid)) == 10
    client = client_for()
    page = client.get("/me").text
    assert "What would you like to see more of?" in page
    assert "Tell us what you want to see more of to get more posts." in page
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
    assert 'action="/me/more"' in page
    assert client.post("/me/more", data={"key": "more-one", "text": "More technical research"}).status_code == 403
    data = {"csrf": "csrf-test", "key": "more-one", "text": "More technical research"}
    assert client.post("/me/more", data=data, follow_redirects=False).status_code == 303
    assert client.post("/me/more", data=data, follow_redirects=False).status_code == 303
    config = digests.config_for(conn, uid)
    assert config["daily_limit"] == 10
    assert config["requested_more_count"] == 10
    assert conn.execute("SELECT count(*) FROM feedback_jobs WHERE user_id=?", (uid,)).fetchone()[0] == 1
    worker.run_cycle(conn, Scorer(), Gemma())
    assert "More technical research" in profiles.markdown(conn, uid)
    assert len(digests.read(conn, uid)) == 20
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
