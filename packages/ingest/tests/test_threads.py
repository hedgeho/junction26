import asyncio
import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path

import httpx
import pytest
from curator_core.config import clear_settings
from curator_core.sources import get_post
from curator_core.testing import add_user
from curator_ingest import threads

TOKEN = "THREADS-SECRET-TOKEN"
CDN = "https://scontent-iad3-1.cdninstagram.com/v/t51/a.jpg?sig=1"


def item(**over: object) -> dict:
    base = {
        "id": "179001",
        "text": "Compilers are fun",
        "media_type": "TEXT_POST",
        "permalink": "https://www.threads.com/@alice/post/abc",
        "timestamp": "2026-11-14T10:00:00+0000",
        "username": "alice",
        "is_reply": False,
        "is_quote_post": False,
    }
    return base | over


def add_reader(conn: sqlite3.Connection, did: str, terms: list[str], seen_days_ago: float = 0) -> None:
    add_user(conn, did)
    seen = int(time.time() - seen_days_ago * 86400)
    conn.execute("UPDATE users SET last_seen_at=? WHERE did=?", (seen, did))
    uid, rev = uuid.uuid4().hex, uuid.uuid4().hex
    conn.execute("INSERT INTO reader_accounts VALUES (?,?,?)", (uid, did, seen))
    conn.execute(
        "INSERT INTO profile_revisions(id,user_id,kind,content_hash,search_terms,origin,created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (rev, uid, "public", "h", json.dumps(terms), "test", seen),
    )
    conn.execute("INSERT INTO profile_heads VALUES (?,?,?)", (uid, "public", rev))


def configure(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    clear_settings()


def client(handler) -> httpx.Client:
    return httpx.Client(base_url="https://graph.threads.com", transport=httpx.MockTransport(handler))


def test_normalize_text_post() -> None:
    record = threads.normalize(item(is_quote_post=True, quoted_post="179000", profile_picture_url=CDN), 123)
    assert record == {
        "platform": "threads",
        "native_id": "179001",
        "author_id": "alice",
        "author_name": "alice",
        "author_handle": "alice",
        "author_avatar": CDN,
        "text": "Compilers are fun",
        "canonical_url": "https://www.threads.com/@alice/post/abc",
        "created_at": "2026-11-14T10:00:00+00:00",
        "observed_at": 123,
        "quote_id": "179000",
        "media": [],
    }


def test_normalize_media() -> None:
    image = threads.normalize(item(media_type="IMAGE", media_url=CDN, alt_text="A chart"), 1)
    assert image["media"] == [{"type": "image", "thumb": CDN, "full": CDN, "alt": "A chart"}]
    video = threads.normalize(item(media_type="VIDEO", media_url="https://x/v.mp4", thumbnail_url=CDN), 1)
    assert video["media"] == [{"type": "video", "thumb": CDN, "alt": ""}]
    children = [
        {"media_type": "IMAGE", "media_url": CDN, "alt_text": "one"},
        {"media_type": "VIDEO", "thumbnail_url": CDN},
    ]
    album = threads.normalize(item(media_type="CAROUSEL_ALBUM", children={"data": children}), 1)
    assert [m["type"] for m in album["media"]] == ["image", "video"]
    link = threads.normalize(item(link_attachment_url="https://example.com/a"), 1)
    assert link["media"] == [{"type": "link", "url": "https://example.com/a"}]


@pytest.mark.parametrize(
    "over",
    [{"is_reply": True}, {"media_type": "REPOST_FACADE"}, {"permalink": None}, {"username": None}, {"id": None}],
)
def test_normalize_skips_unusable_items(over: dict) -> None:
    assert threads.normalize(item(**over), 1) is None


def test_terms_are_public_deduplicated_and_from_active_readers(conn: sqlite3.Connection) -> None:
    add_reader(conn, "did:plc:a", ["Compilers", "rust"])
    add_reader(conn, "did:plc:b", ["compilers", "gardening"])
    add_reader(conn, "did:plc:old", ["stale"], seen_days_ago=30)
    assert threads.search_terms(conn) == ["compilers", "gardening", "rust"]


def test_collect_sends_documented_query_and_imports(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)
    add_reader(conn, "did:plc:a", ["compilers"])
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": [item(), item(id="2", is_reply=True)]})

    assert threads.collect_once(conn, client(handler), TOKEN) == 1
    params = seen[0].url.params
    assert seen[0].url.path == "/v1.0/keyword_search"
    assert (params["q"], params["search_type"], params["limit"], params["access_token"]) == (
        "compilers",
        "RECENT",
        "100",
        TOKEN,
    )
    assert int(params["since"]) < time.time()
    assert "id" in params["fields"].split(",") and "is_reply" in params["fields"].split(",")
    assert "owner" not in params["fields"]
    assert get_post(conn, "threads:179001").author_key == "threads:alice"
    # The next cycle resumes from the last run rather than the full retention window.
    threads.collect_once(conn, client(handler), TOKEN)
    assert int(seen[1].url.params["since"]) > int(params["since"]) + 3600 * 40


def test_terms_rotate_under_the_per_cycle_cap(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch, THREADS_TERMS_PER_CYCLE="2")
    add_reader(conn, "did:plc:a", ["a", "b", "c", "d", "e"])
    queried: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        queried.append(request.url.params["q"])
        return httpx.Response(200, json={"data": []})

    for _ in range(3):
        threads.collect_once(conn, client(handler), TOKEN)
    assert queried == ["a", "b", "c", "d", "e", "a"]


def test_one_failing_term_does_not_stop_the_rest(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    configure(monkeypatch)
    add_reader(conn, "did:plc:a", ["bad", "good"])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["q"] == "bad":
            return httpx.Response(500, text=f"oops {TOKEN}")
        return httpx.Response(200, json={"data": [item()]})

    with caplog.at_level(logging.INFO):
        assert threads.collect_once(conn, client(handler), TOKEN) == 1
    assert "500" in caplog.text and TOKEN not in caplog.text


def test_rate_limit_stops_the_cycle(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)
    add_reader(conn, "did:plc:a", ["a", "b"])
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params["q"])
        return httpx.Response(429, json={"error": {"code": 4}})

    assert threads.collect_once(conn, client(handler), TOKEN) == 0
    assert calls == ["a"]


def test_token_file_wins_unless_the_env_token_changed(tmp_path: Path) -> None:
    path = tmp_path / "threads_token.json"
    assert threads.load_token(path, "seed") == "seed"
    threads.save_token(path, "refreshed", seed="seed", now=100)
    assert threads.load_token(path, "seed") == "refreshed"
    assert threads.load_token(path, "rotated-by-operator") == "rotated-by-operator"
    assert threads.load_token(path, None) is None


def test_refresh_saves_new_token_and_failure_keeps_the_old_one(tmp_path: Path) -> None:
    path = tmp_path / "threads_token.json"

    def ok(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/refresh_access_token"
        assert (request.url.params["grant_type"], request.url.params["access_token"]) == ("th_refresh_token", "seed")
        return httpx.Response(200, json={"access_token": "new", "token_type": "bearer", "expires_in": 5183944})

    def too_early(request: httpx.Request) -> httpx.Response:
        pytest.fail("refreshed too early")

    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "nope"}})

    assert threads.refresh_if_due(client(ok), path, "seed", now=1_000_000) == "new"
    assert threads.load_token(path, "seed") == "new"
    assert threads.refresh_if_due(client(too_early), path, "seed", now=1_000_100) == "new"
    assert threads.refresh_if_due(client(broken), path, "seed", now=1_000_000 + 30 * 86400) == "new"


def test_token_is_redacted_from_httpx_logs(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="httpx"):
        logging.getLogger("httpx").info("GET https://g/v1.0/x?q=a&access_token=%s HTTP/1.1 200", TOKEN)
    assert TOKEN not in caplog.text and "access_token=REDACTED" in caplog.text


def test_normalize_tolerates_unexpected_shapes() -> None:
    assert threads.normalize("not a post", 1) is None
    assert threads.normalize(item(id=179001), 1) is None
    assert threads.normalize(item(quoted_post={"id": "179000"}), 1)["quote_id"] == "179000"
    assert threads.normalize(item(quoted_post={"x": 1}), 1)["quote_id"] is None
    assert threads.normalize(item(text=None), 1)["text"] == ""
    odd = item(media_type="CAROUSEL_ALBUM", children={"data": ["junk", {"media_type": "IMAGE", "media_url": CDN}]})
    assert [m["type"] for m in threads.normalize(odd, 1)["media"]] == ["image"]
    assert threads.normalize(item(children=["junk"]), 1)["media"] == []


def test_one_invalid_record_does_not_drop_the_term(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)
    add_reader(conn, "did:plc:a", ["compilers"])
    bad = item(id="2", permalink="https://evil.example/@alice/post/x")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [bad, item(), "junk"]})

    assert threads.collect_once(conn, client(handler), TOKEN) == 1
    assert get_post(conn, "threads:179001") is not None
    assert get_post(conn, "threads:2") is None
    assert threads.db.kv_get(conn, "threads:since:compilers") is not None


def test_since_is_clamped_to_retention(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch, RETENTION_HOURS="48")
    add_reader(conn, "did:plc:a", ["compilers"])
    threads.db.kv_set(conn, "threads:since:compilers", "1000")
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(int(request.url.params["since"]))
        return httpx.Response(200, json={"data": []})

    threads.collect_once(conn, client(handler), TOKEN)
    assert seen[0] >= time.time() - 48 * 3600 - 5


@pytest.mark.parametrize("status,code", [(400, 4), (403, 17), (400, 613)])
def test_graph_throttle_codes_stop_the_cycle_and_keep_the_term(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, status: int, code: int
) -> None:
    configure(monkeypatch, THREADS_TERMS_PER_CYCLE="3")
    add_reader(conn, "did:plc:a", ["a", "b", "c"])
    queried: list[str] = []
    limited = {"b"}

    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params["q"]
        queried.append(q)
        if q in limited:
            return httpx.Response(status, json={"error": {"code": code}})
        return httpx.Response(200, json={"data": []})

    threads.collect_once(conn, client(handler), TOKEN)
    assert queried == ["a", "b"]
    limited.clear()
    threads.collect_once(conn, client(handler), TOKEN)
    assert queried[2:] == ["b", "c", "a"]


def test_other_400s_are_skipped_not_treated_as_throttling(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(monkeypatch)
    add_reader(conn, "did:plc:a", ["a", "b"])
    queried: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        queried.append(request.url.params["q"])
        return httpx.Response(400, json={"error": {"code": 100}})

    threads.collect_once(conn, client(handler), TOKEN)
    assert queried == ["a", "b"]


def test_network_errors_advance_past_the_term(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch, THREADS_TERMS_PER_CYCLE="2")
    add_reader(conn, "did:plc:a", ["a", "b", "c"])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["q"] == "a":
            raise httpx.ConnectError("down")
        return httpx.Response(200, json={"data": []})

    threads.collect_once(conn, client(handler), TOKEN)
    assert threads.db.kv_get(conn, "threads:cursor") == "2"


def test_restart_waits_for_the_remaining_interval(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch, THREADS_POLL_INTERVAL_S="1800")
    now = int(time.time())
    assert threads.seconds_until_due(conn, now) <= 0
    threads.collect_once(conn, client(lambda r: httpx.Response(200, json={"data": []})), TOKEN)
    assert 1790 <= threads.seconds_until_due(conn, now + 5) <= 1800


def test_run_survives_a_failing_cycle(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch, THREADS_ACCESS_TOKEN=TOKEN)
    cycles: list[int] = []
    sleeps: list[float] = []

    def broken(*args: object) -> int:
        cycles.append(1)
        raise sqlite3.OperationalError("database is locked")

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(threads, "refresh_if_due", lambda *a: TOKEN)
    monkeypatch.setattr(threads, "collect_once", broken)
    monkeypatch.setattr(threads.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(threads.run())
    assert len(cycles) == 2 and sleeps == [1800, 1800]
