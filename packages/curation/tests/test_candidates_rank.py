import time

from curator_core.testing import add_user, insert_post
from curator_curation import candidates, rank

USER = "did:user"


def seed(conn):
    for i in range(6):
        insert_post(conn, "did:friend", f"f{i}", f"friend post number {i} about rust", age_s=i * 60)
    insert_post(conn, "did:stranger", "s1", "stranger talks about rust compilers")
    insert_post(conn, "did:blocked", "b1", "blocked account rust post")
    add_user(conn, USER, prompt="rust", follows=["did:friend", "did:blocked"])
    now = int(time.time())
    for cat, summary in (("search_term", "rust compilers"), ("author_block", "did:blocked")):
        conn.execute(
            "INSERT INTO preferences(user_did, category, summary, content, created_at) VALUES (?,?,?,?,?)",
            (USER, cat, summary, summary, now),
        )


def test_candidates_mix_sources_and_respect_blocks(conn):
    seed(conn)
    uris = {c["uri"] for c in candidates.candidates(conn, USER, 50)}
    assert "at://did:stranger/app.bsky.feed.post/s1" in uris  # from FTS search
    assert sum("did:friend" in u for u in uris) == 6  # from follows
    assert not any("did:blocked" in u for u in uris)


def test_rank_diversifies_and_drops_requested_less(conn):
    seed(conn)
    now = int(time.time())
    for c in candidates.candidates(conn, USER, 50):
        conn.execute("INSERT INTO scores VALUES (?, ?, 0.9, 'r', 'm', ?)", (USER, c["uri"], now))
    conn.execute(
        "INSERT INTO interactions VALUES (?, ?, 'app.bsky.feed.defs#requestLess', ?)",
        (USER, "at://did:friend/app.bsky.feed.post/f0", now),
    )
    rank.rank_user(conn, USER)
    feed = [
        r["post_uri"]
        for r in conn.execute("SELECT post_uri FROM feed_items WHERE user_did = ? ORDER BY position", (USER,))
    ]
    assert "at://did:friend/app.bsky.feed.post/f0" not in feed
    assert feed.index("at://did:stranger/app.bsky.feed.post/s1") < 3  # not buried under the prolific friend
