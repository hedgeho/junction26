"""Per-source cache invalidation and retries, without model/network calls."""

import uuid

from curator_core.sources import get_post
from curator_core.testing import add_user, insert_post
from curator_curation import notes, profiles
from curator_curation.llm.schemas import NoteMatches, NotePostMatch


class RecordingModel:
    model = "test-model"

    def __init__(self, *, empty=False, fail=False):
        self.calls = []
        self.empty, self.fail = empty, fail
        self.extra = None

    def match_note(self, posts, chunks, profile):
        self.calls.append([post["post_key"] for post in posts])
        if self.fail:
            raise ValueError("unavailable")
        if self.empty:
            return NoteMatches(items=[])
        keys = [self.extra] if self.extra else self.calls[-1]
        return NoteMatches(
            items=[NotePostMatch(post_key=key, chunk_id=chunks[0]["chunk_id"], relevance=0.9) for key in keys]
        )


def prepare(conn):
    add_user(conn, "did:plc:cache-reader")
    uid = profiles.bootstrap(conn, "did:plc:cache-reader")
    nid = notes.import_note(conn, uid, "Compilers", "Compiler evaluation methods")
    key = "bluesky:" + insert_post(conn, "did:plc:cache-author", "one", "Compiler benchmarks")
    return uid, nid, key


def match(conn, uid, keys, model):
    run = uuid.uuid4().hex
    conn.execute("INSERT INTO digest_runs VALUES (?,?,?,?,?,?)", (run, uid, "2026-11-15", "baseline-v1", 1, 1))
    posts = [get_post(conn, key) for key in keys]
    conn.executemany(
        "INSERT INTO digest_items VALUES (?,?,?,?)",
        [(run, i, post.key, post.content_hash) for i, post in enumerate(posts)],
    )
    return notes.match_feed(conn, uid, run, posts, model)


def test_new_feed_post_does_not_reclassify_unchanged_pairs(conn):
    uid, _, first = prepare(conn)
    model = RecordingModel()
    assert match(conn, uid, [first], model) == 1
    second = "bluesky:" + insert_post(conn, "did:plc:cache-author", "two", "Another compiler benchmark")
    assert match(conn, uid, [first, second], model) == 1
    assert model.calls == [[first], [second]]
    assert match(conn, uid, [second, first], model) == 0
    assert notes.matches_for(conn, uid, get_post(conn, first))


def test_successful_empty_decisions_are_cached(conn):
    uid, _, key = prepare(conn)
    model = RecordingModel(empty=True)
    for _ in range(3):
        assert match(conn, uid, [key], model) == 0
    assert model.calls == [[key]]


def test_post_note_model_and_profile_changes_invalidate_cache(conn):
    uid, nid, key = prepare(conn)
    model = RecordingModel()
    match(conn, uid, [key], model)
    conn.execute("UPDATE posts SET text='Changed compiler benchmarks' WHERE uri=?", (key.removeprefix("bluesky:"),))
    match(conn, uid, [key], model)
    notes.delete_note(conn, uid, nid)
    notes.import_note(conn, uid, "Compilers", "Changed original compiler passage")
    match(conn, uid, [key], model)
    model.model = "other-model"
    match(conn, uid, [key], model)
    head = profiles.head(conn, uid, "private")
    profiles.save(conn, uid, "private", "# Interests\nCompiler research", head["id"] if head else None)
    match(conn, uid, [key], model)
    assert model.calls == [[key]] * 5


def test_cached_post_cannot_be_returned_in_a_different_request(conn):
    uid, _, first = prepare(conn)
    model = RecordingModel()
    match(conn, uid, [first], model)
    second = "bluesky:" + insert_post(conn, "did:plc:cache-author", "two", "New post")
    model.extra = first
    assert match(conn, uid, [first, second], model) == 0
    assert model.calls[-1] == [second]
    assert conn.execute("SELECT count(*) FROM note_match_batches WHERE state='failed'").fetchone()[0] == 1
    assert not notes.matches_for(conn, uid, get_post(conn, second))


def test_changing_feed_does_not_reset_failed_pair_retry_budget(conn):
    uid, _, first = prepare(conn)
    model = RecordingModel(fail=True)
    for _ in range(3):
        match(conn, uid, [first], model)
    second = "bluesky:" + insert_post(conn, "did:plc:cache-author", "two", "New post")
    match(conn, uid, [first, second], model)
    assert model.calls == [[first], [first], [first], [second]]
