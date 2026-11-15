from sqlite3 import Connection

import pytest
from curator_core.config import clear_settings
from curator_web.app import create_app
from fastapi.testclient import TestClient


def anonymous() -> TestClient:
    return TestClient(create_app(), base_url="https://feed.example.com")


@pytest.mark.parametrize("path", ["/terms", "/data-deletion", "/privacy"])
def test_public_policy_pages_need_no_sign_in(conn: Connection, path: str) -> None:
    assert anonymous().get(path).status_code == 200


def test_data_deletion_page_says_what_is_kept_and_for_how_long(conn: Connection) -> None:
    text = anonymous().get("/data-deletion").text
    assert "Threads" in text and "48 hours" in text
    assert "mailto:" not in text


def test_data_deletion_page_shows_contact_when_configured(conn: Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTACT_EMAIL", "ops@example.com")
    clear_settings()
    assert 'href="mailto:ops@example.com"' in anonymous().get("/data-deletion").text


def test_deauthorize_callback_accepts_meta_post_without_csrf(conn: Connection) -> None:
    response = anonymous().post("/threads/deauthorize", data={"signed_request": "x.y"})
    assert response.status_code == 200
    assert response.json() == {}


def test_data_deletion_callback_returns_status_url_and_unique_codes(conn: Connection) -> None:
    client = anonymous()
    first = client.post("/threads/data-deletion", data={"signed_request": "x.y"}).json()
    second = client.post("/threads/data-deletion", data={"signed_request": "x.y"}).json()
    assert first["url"] == "https://feed.example.com/data-deletion"
    assert first["confirmation_code"] and first["confirmation_code"] != second["confirmation_code"]


def test_callbacks_are_post_only(conn: Connection) -> None:
    assert anonymous().get("/threads/data-deletion").status_code == 405
