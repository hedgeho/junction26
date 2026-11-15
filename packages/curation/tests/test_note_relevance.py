import uuid

from curator_core import db
from curator_core.sources import get_post
from curator_core.testing import add_user, insert_post
from curator_curation import notes, profiles
from curator_curation.llm.schemas import NoteMatches, NotePostMatch


def test_best_direct_passage_is_selected_independently_for_each_post(conn):
    add_user(conn, "did:plc:note-reader")
    uid = profiles.bootstrap(conn, "did:plc:note-reader")
    notes.import_note(conn, uid, "Compilers", "Compiler benchmarks and reproducibility")
    notes.import_note(conn, uid, "Safety", "AI safety and alignment evaluation")
    posts = [
        get_post(conn, "bluesky:" + insert_post(conn, "did:plc:author", "compiler", "Compiler benchmarks")),
        get_post(conn, "bluesky:" + insert_post(conn, "did:plc:author", "safety", "AI safety alignment")),
    ]
    run = uuid.uuid4().hex
    conn.execute("INSERT INTO digest_runs VALUES (?,?,?,?,?,?)", (run, uid, "2026-11-15", "baseline-v1", 1, 1))
    conn.executemany(
        "INSERT INTO digest_items VALUES (?,?,?,?)",
        [(run, index, post.key, post.content_hash) for index, post in enumerate(posts)],
    )

    class Model:
        model = "test"

        def match_note(self, candidates, chunks, profile):
            title = chunks[0]["title"]
            return NoteMatches(
                items=[
                    NotePostMatch(
                        post_key=post["post_key"],
                        chunk_id=chunks[0]["chunk_id"],
                        relevance=0.95 if ("Compiler" in post["text"]) == (title == "Compilers") else 0.81,
                    )
                    for post in candidates
                ]
            )

    notes.match_feed(conn, uid, run, posts, Model())
    assert [match["title"] for match in notes.matches_for(conn, uid, posts[0])] == ["Compilers"]
    assert [match["title"] for match in notes.matches_for(conn, uid, posts[1])] == ["Safety"]


def test_old_or_low_relevance_matches_are_not_shown(conn):
    add_user(conn, "did:plc:note-reader")
    uid = profiles.bootstrap(conn, "did:plc:note-reader")
    note_id = notes.import_note(conn, uid, "Note", "Original passage")
    chunk_id = conn.execute("SELECT id FROM note_chunks WHERE note_id=?", (note_id,)).fetchone()[0]
    post = get_post(conn, "bluesky:" + insert_post(conn, "did:plc:author", "one", "A post"))
    conn.execute(
        "INSERT INTO note_matches VALUES (?,?,?,?,?,?)", (uid, post.key, post.content_hash, "", chunk_id, 0.99)
    )
    assert notes.matches_for(conn, uid, post) == []
    notes.prepare_matches(conn, uid)
    assert conn.execute("SELECT count(*) FROM note_matches WHERE user_id=?", (uid,)).fetchone()[0] == 0
    conn.execute(
        "INSERT INTO note_matches VALUES (?,?,?,?,?,?)", (uid, post.key, post.content_hash, "", chunk_id, 0.79)
    )
    assert db.kv_get(conn, "note-match-version:" + uid) == notes.MATCH_VERSION
    assert notes.matches_for(conn, uid, post) == []


def test_shortlist_includes_distinct_posts_topics(conn):
    add_user(conn, "did:plc:note-reader")
    uid = profiles.bootstrap(conn, "did:plc:note-reader")
    text = ("compiler " * 125 * 6) + ("orchid " * 142)
    notes.import_note(conn, uid, "Mixed topics", text)
    posts = [
        get_post(conn, "bluesky:" + insert_post(conn, "did:plc:author", "compiler", "Compiler compiler")),
        get_post(conn, "bluesky:" + insert_post(conn, "did:plc:author", "orchid", "Orchid orchid")),
    ]
    run = uuid.uuid4().hex
    conn.execute("INSERT INTO digest_runs VALUES (?,?,?,?,?,?)", (run, uid, "2026-11-15", "baseline-v1", 1, 1))
    conn.executemany(
        "INSERT INTO digest_items VALUES (?,?,?,?)",
        [(run, index, post.key, post.content_hash) for index, post in enumerate(posts)],
    )

    class Model:
        model = "test"

        def __init__(self):
            self.chunks = []

        def match_note(self, candidates, chunks, profile):
            self.chunks = chunks
            return NoteMatches(items=[])

    model = Model()
    notes.match_feed(conn, uid, run, posts, model)
    assert any("compiler" in chunk["text"] for chunk in model.chunks)
    assert any("orchid" in chunk["text"] for chunk in model.chunks)


def test_note_shortlist_covers_multiple_posts(conn, monkeypatch):
    from curator_curation import notes as notes_module

    add_user(conn, "did:plc:note-reader")
    uid = profiles.bootstrap(conn, "did:plc:note-reader")
    for index in range(15):
        notes.import_note(conn, uid, f"Compiler {index}", "compiler benchmark compiler")
    notes.import_note(conn, uid, "Orchids", "orchid pollination")
    posts = [
        get_post(conn, "bluesky:" + insert_post(conn, "did:plc:author", "compiler", "Compiler benchmark")),
        get_post(conn, "bluesky:" + insert_post(conn, "did:plc:author", "orchid", "Orchid pollination")),
    ]
    run = uuid.uuid4().hex
    conn.execute("INSERT INTO digest_runs VALUES (?,?,?,?,?,?)", (run, uid, "2026-11-15", "baseline-v1", 1, 1))
    conn.executemany(
        "INSERT INTO digest_items VALUES (?,?,?,?)",
        [(run, index, post.key, post.content_hash) for index, post in enumerate(posts)],
    )
    settings = notes_module.settings()
    monkeypatch.setattr(notes_module, "settings", lambda: settings.model_copy(update={"note_match_top_notes": 2}))

    class Model:
        model = "test"

        def __init__(self):
            self.titles = []

        def match_note(self, candidates, chunks, profile):
            self.titles.append(chunks[0]["title"])
            return NoteMatches(items=[])

    model = Model()
    notes.match_feed(conn, uid, run, posts, model)
    assert "Orchids" in model.titles
    assert any(title.startswith("Compiler") for title in model.titles)
    assert len(model.titles) == 2
