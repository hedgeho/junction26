from curator_core import db
from curator_core.testing import add_user
from curator_curation import health, profiles

from packages.web.tests.test_reader import DID, client_for, seed


def test_failed_curation_is_hidden_when_posts_are_available(conn, monkeypatch):
    seed(conn)
    monkeypatch.setattr(
        health, "filtering_status", lambda conn, uid: {"state": "failed", "label": health.LABELS["failed"]}
    )

    response = client_for().get("/me")

    assert '<div class="notice" role="status" hidden><span id="curation-pending">' in response.text
    assert "Original human post" in response.text
    assert "Filtering temporarily unavailable. Retrying." not in response.text


def test_failed_curation_explains_an_empty_feed(conn, monkeypatch):
    add_user(conn, DID, "profile")
    uid = profiles.bootstrap(conn, DID)
    db.kv_set(conn, "setup:" + uid, "done")
    monkeypatch.setattr(
        health, "filtering_status", lambda conn, uid: {"state": "failed", "label": health.LABELS["failed"]}
    )

    response = client_for().get("/me")

    assert "Your selection is temporarily unavailable." in response.text
    assert "Filtering temporarily unavailable. Retrying." not in response.text
