import json
import time

from curator_core import db
from curator_core.testing import add_user
from curator_ingest import follows, jetstream


def post_evt(did, rkey, text, t, **rec):
    return {
        "did": did,
        "time_us": t * 1_000_000,
        "kind": "commit",
        "commit": {
            "operation": "create",
            "collection": jetstream.POST,
            "rkey": rkey,
            "cid": "bafy",
            "record": {"text": text, "langs": ["en"], **rec},
        },
    }


def like_evt(did, subject, t):
    return {
        "did": did,
        "time_us": t * 1_000_000,
        "kind": "commit",
        "commit": {
            "operation": "create",
            "collection": jetstream.LIKE,
            "rkey": "x",
            "record": {"subject": {"uri": subject}},
        },
    }


def test_ingest_posts_likes_replies_deletes(conn):
    now = int(time.time())
    b = jetstream.Batch()
    jetstream.handle_event(post_evt("did:a", "1", "hello compilers world", now), b, set())
    jetstream.handle_event(post_evt("did:b", "2", "nein", now, langs=["de"]), b, {"en"})
    parent = "at://did:a/app.bsky.feed.post/1"
    jetstream.handle_event(post_evt("did:c", "3", "a reply here", now, reply={"parent": {"uri": parent}}), b, set())
    for i in range(3):
        jetstream.handle_event(like_evt(f"did:l{i}", parent, now), b, set())
    jetstream.flush(conn, b)

    row = conn.execute("SELECT * FROM posts WHERE uri = ?", (parent,)).fetchone()
    assert (row["likes"], row["replies"]) == (3, 1)
    assert conn.execute("SELECT count(*) FROM posts").fetchone()[0] == 2  # 'de' post filtered
    assert conn.execute("SELECT count(*) FROM posts_fts WHERE posts_fts MATCH 'compilers'").fetchone()[0] == 1
    assert db.kv_get(conn, jetstream.CURSOR_KEY) == str(now * 1_000_000)

    b = jetstream.Batch()
    delete = {"operation": "delete", "collection": jetstream.POST, "rkey": "1"}
    jetstream.handle_event({"did": "did:a", "time_us": 1, "kind": "commit", "commit": delete}, b, set())
    jetstream.flush(conn, b)
    assert conn.execute("SELECT count(*) FROM posts_fts WHERE posts_fts MATCH 'compilers'").fetchone()[0] == 0


def test_stale_readers(conn):
    add_user(conn, "did:fresh")
    conn.execute("INSERT INTO users(did, created_at, last_seen_at) VALUES ('did:new', 0, unixepoch())")
    conn.execute("INSERT INTO users(did, created_at, last_seen_at) VALUES ('did:gone', 0, 0)")
    assert follows.stale_readers(conn) == ["did:new"]


def test_ingest_keeps_media_references(conn):
    now = int(time.time())
    cid = "bafkreigh2akiscaildcqabsyg3dfr6chu3fgpregiymsck7e7aqa4s52zy"
    images = {
        "$type": "app.bsky.embed.images",
        "images": [{"alt": "Benchmark plot", "image": {"$type": "blob", "ref": {"$link": cid}}}],
    }
    b = jetstream.Batch()
    jetstream.handle_event(post_evt("did:plc:a", "1", "look", now, embed=images), b, set())
    jetstream.flush(conn, b)
    row = conn.execute("SELECT embed_kind, media FROM posts").fetchone()
    assert row["embed_kind"] == "images"
    assert json.loads(row["media"])[0]["alt"] == "Benchmark plot"


def test_ingest_stores_text_signature_for_campaign_detection(conn):
    now = int(time.time())
    b = jetstream.Batch()
    jetstream.handle_event(post_evt("did:a", "1", "Claim your FREE airdrop now: https://a.example", now), b, set())
    jetstream.handle_event(post_evt("did:b", "2", "claim your free AIRDROP now!! https://b.example", now), b, set())
    jetstream.handle_event(post_evt("did:c", "3", "gm", now), b, set())
    jetstream.flush(conn, b)
    sigs = [r[0] for r in conn.execute("SELECT text_sig FROM posts ORDER BY uri")]
    assert sigs[0] == sigs[1] and sigs[0] is not None and sigs[2] is None


def test_likes_and_reposts_by_followed_accounts_are_recorded(conn):
    now = int(time.time())
    b = jetstream.Batch()
    subject = "at://did:a/app.bsky.feed.post/1"
    followed = frozenset({"did:friend"})
    jetstream.handle_event(like_evt("did:friend", subject, now), b, set(), followed)
    jetstream.handle_event(like_evt("did:stranger", subject, now), b, set(), followed)
    jetstream.flush(conn, b)
    rows = conn.execute("SELECT actor_did, subject_uri, kind FROM followed_engagement").fetchall()
    assert [tuple(r) for r in rows] == [("did:friend", subject, "like")]
