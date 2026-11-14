"""Pytest fixtures and seed helpers shared by every package's tests.

Seeding goes through plain SQL against the contract tables, so curation and web
tests don't depend on the ingest package.
"""

import time

import pytest

from . import db
from .config import clear_settings


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HOSTNAME", "feed.example.com")
    monkeypatch.setenv("PUBLISHER_DID", "did:plc:publisher")
    # Never use developer/provider credentials from local dotenv files in tests.
    for key in ("GEMINI_API_KEY", "GEMINI_MODEL", "GEMMA_URL", "GEMMA_TOKEN"):
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("LABEL_CHECKS", "false")
    clear_settings()
    yield db.connect()
    clear_settings()


def insert_post(conn, author: str, rkey: str, text: str, age_s: int = 0, **cols) -> str:
    uri = f"at://{author}/app.bsky.feed.post/{rkey}"
    row = {
        "uri": uri,
        "cid": "bafy",
        "author_did": author,
        "text": text,
        "langs": "en",
        "indexed_at": int(time.time()) - age_s,
    } | cols
    conn.execute(f"INSERT INTO posts({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
    return uri


def add_user(conn, did: str, prompt: str = "", follows: list[str] = ()) -> None:
    now = int(time.time())
    conn.execute(
        "INSERT INTO users(did, prompt, created_at, last_seen_at) VALUES (?, ?, ?, ?)", (did, prompt, now, now)
    )
    conn.executemany("INSERT INTO follows VALUES (?, ?)", [(did, f) for f in follows])
    conn.execute("INSERT INTO follow_sync VALUES (?, ?)", (did, now))
