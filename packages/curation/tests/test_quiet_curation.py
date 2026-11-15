import json
import time
from types import SimpleNamespace

import pytest
from curator_core.testing import add_user, insert_post
from curator_curation import digests, jobs, notes, profiles, worker
from curator_curation.llm.gemini import GeminiScorer, PublicCandidate, PublicScoringRequest
from curator_curation.llm.schemas import Assessment, Assessments, ChunkMatch, Matches, ProfileProposal

DID = "did:plc:reader"


class FakeGemma:
    def update_profile(self, current, feedback):
        return ProfileProposal(markdown=current + "\n" + feedback, search_terms=["compilers"])

    def match(self, post, chunks, private_profile):
        return (
            Matches(items=[ChunkMatch(chunk_id=chunks[0]["chunk_id"], relevance=0.9)]) if chunks else Matches(items=[])
        )


class FakeScorer:
    def __init__(self):
        self.requests = []

    def score(self, request):
        self.requests.append(request)
        return Assessments(
            items=[
                Assessment(post_key=p.post_key, decision="include", relevance=0.9, substance=0.8, opportunity=0.7)
                for p in request.candidates
            ]
        )


def setup(conn):
    add_user(conn, DID, "Practical compilers, not launch hype")
    uid = profiles.bootstrap(conn, DID)
    uri = insert_post(conn, "did:plc:author", "one", "A concrete compiler evaluation with reproducible results")
    return uid, "bluesky:" + uri


def test_quiet_feedback_is_idempotent_and_revisioned(conn):
    uid, _ = setup(conn)
    original = profiles.head(conn, uid)["id"]
    job = jobs.enqueue(conn, uid, "public", "Less fundraising; still interested in founders' decisions", "submission-1")
    assert job == jobs.enqueue(conn, uid, "public", "different accidental replay", "submission-1")
    assert jobs.process_one(conn, FakeGemma())
    state = conn.execute("SELECT * FROM feedback_jobs WHERE id=?", (job,)).fetchone()
    assert state["state"] == "applied"
    assert state["applied_revision"] != original
    assert "Practical compilers" in profiles.markdown(conn, uid)
    assert "still interested" in profiles.markdown(conn, uid)
    assert not jobs.process_one(conn, FakeGemma())


def test_profile_compare_and_swap_and_exact_unicode(conn):
    uid, _ = setup(conn)
    parent = profiles.head(conn, uid)["id"]
    body = "# Interests\n\nSuomi \U0001f98b\n"
    revision = profiles.save(conn, uid, "public", body, parent)
    with pytest.raises(profiles.RevisionConflict):
        profiles.save(conn, uid, "public", "stale edit", parent)
    assert profiles.head(conn, uid)["id"] == revision
    assert profiles.markdown(conn, uid) == body


def test_job_crash_recovery_does_not_apply_twice(conn):
    uid, _ = setup(conn)
    job = jobs.enqueue(conn, uid, "public", "more compilers", "recover")
    parent = profiles.head(conn, uid)["id"]
    rev = profiles.save(conn, uid, "public", "# Already applied", parent, origin="feedback:" + job)
    assert jobs.process_one(conn, FakeGemma())
    assert profiles.head(conn, uid)["id"] == rev
    assert conn.execute("SELECT state FROM feedback_jobs WHERE id=?", (job,)).fetchone()[0] == "applied"


def test_provider_failure_preserves_profile_and_sanitizes_error(conn):
    uid, _ = setup(conn)
    original = profiles.markdown(conn, uid)
    jid = jobs.enqueue(conn, uid, "private", "private sentinel", "fail")

    class Broken:
        def update_profile(self, *args):
            raise RuntimeError("sensitive provider body")

    for _ in range(3):
        jobs.process_one(conn, Broken())
    row = conn.execute("SELECT state,error_code FROM feedback_jobs WHERE id=?", (jid,)).fetchone()
    assert tuple(row) == ("failed", "update_failed")
    assert profiles.markdown(conn, uid) == original


def test_private_notes_and_private_feedback_never_enter_public_requests(conn):
    uid, _ = setup(conn)
    notes.import_note(conn, uid, "Private source", "PRIVATE_SENTINEL original writing \U0001f98b")
    jobs.enqueue(conn, uid, "private", "PRIVATE_INTEREST_SENTINEL", "private")
    jobs.process_one(conn, FakeGemma())
    scorer = FakeScorer()
    worker.run_cycle(conn, scorer, FakeGemma())
    assert len(scorer.requests) == 1
    payload = scorer.requests[0].payload()
    assert "PRIVATE_SENTINEL" not in payload
    assert "PRIVATE_INTEREST_SENTINEL" not in payload
    data = json.loads(payload)
    assert set(data) == {"explicit_profile", "posts"}
    assert set(data["posts"][0]) == {"post_key", "platform", "text", "context", "is_reply", "is_quote", "media"}
    assert digests.read(conn, uid)[0]["notes"][0]["text"] == "PRIVATE_SENTINEL original writing \U0001f98b"


def test_profile_change_rescores_existing_content_and_daily_snapshot_is_stable(conn):
    uid, _ = setup(conn)
    scorer = FakeScorer()
    worker.run_cycle(conn, scorer, FakeGemma())
    first = digests.latest(conn, uid)["id"]
    worker.run_cycle(conn, scorer, FakeGemma())
    assert len(scorer.requests) == 1
    assert digests.latest(conn, uid)["id"] == first
    parent = profiles.head(conn, uid)["id"]
    profiles.save(conn, uid, "public", "# Only deep compiler evaluations", parent)
    assert digests.read(conn, uid)  # Real candidates remain available while rescoring.
    worker.run_cycle(conn, scorer, FakeGemma())
    assert len(scorer.requests) == 2
    assert digests.latest(conn, uid)["id"] != first


def test_source_edit_deletion_blocks_and_dismissal_apply_to_existing_digest(conn):
    uid, key = setup(conn)
    scorer = FakeScorer()
    worker.run_cycle(conn, scorer)
    assert len(digests.read(conn, uid)) == 1
    conn.execute("UPDATE reader_settings SET blocked_authors=? WHERE user_id=?", ('["bluesky:did:plc:author"]', uid))
    assert digests.read(conn, uid) == []
    conn.execute("UPDATE reader_settings SET blocked_authors='[]' WHERE user_id=?", (uid,))
    conn.execute("UPDATE posts SET text='Changed after assessment' WHERE uri=?", (key.removeprefix("bluesky:"),))
    assert digests.read(conn, uid) == []
    conn.execute("UPDATE reader_settings SET refresh_requested=1 WHERE user_id=?", (uid,))
    worker.run_cycle(conn, scorer)
    assert digests.read(conn, uid)[0]["text"] == "Changed after assessment"
    conn.execute("DELETE FROM posts WHERE uri=?", (key.removeprefix("bluesky:"),))
    assert digests.read(conn, uid) == []


def test_limit_deduplication_and_empty_selection(conn):
    uid, _ = setup(conn)
    for i in range(5):
        insert_post(conn, "did:plc:other" + str(i), str(i), "The identical cross-posted technical announcement")
    conn.execute("UPDATE reader_settings SET daily_limit=2 WHERE user_id=?", (uid,))
    worker.run_cycle(conn, FakeScorer())
    assert len(digests.read(conn, uid)) == 2
    assert len({p["text"] for p in digests.read(conn, uid)}) == 2
    conn.execute("DELETE FROM assessments_v2")
    digests.build(conn, uid, worker.scorer_version())
    assert digests.read(conn, uid) == []


def test_note_deletion_and_cross_user_ownership(conn):
    uid, _ = setup(conn)
    note = notes.import_note(conn, uid, "Original", "Exact original caf\u00e9 \U0001f98b writing")
    worker.run_cycle(conn, FakeScorer(), FakeGemma())
    assert digests.read(conn, uid)[0]["notes"]
    notes.delete_note(conn, "someone-else", note)
    assert digests.read(conn, uid)[0]["notes"]
    notes.delete_note(conn, uid, note)
    assert digests.read(conn, uid)[0]["notes"] == []
    assert not conn.execute("SELECT 1 FROM note_chunks").fetchone()
    assert not conn.execute("SELECT 1 FROM note_matches").fetchone()


def test_invalid_model_matches_never_become_source_text(conn):
    uid, _ = setup(conn)
    notes.import_note(conn, uid, "Original", "Original words only")

    class Invented(FakeGemma):
        def match(self, *args):
            return Matches(items=[ChunkMatch(chunk_id="invented wording", relevance=1)])

    worker.run_cycle(conn, FakeScorer(), Invented())
    assert digests.read(conn, uid)[0]["notes"] == []


def test_gemini_rejects_duplicate_missing_or_foreign_results(monkeypatch):
    request = PublicScoringRequest("# Explicit", (PublicCandidate("x:1", "x", "text", "", False, False),))
    model = object.__new__(GeminiScorer)
    model.model = "test-model"

    def respond(**kwargs):
        return SimpleNamespace(
            text=json.dumps(
                {
                    "items": [
                        {
                            "post_key": "x:wrong",
                            "decision": "include",
                            "relevance": 0.9,
                            "substance": 0.8,
                            "opportunity": 0.7,
                        }
                    ]
                }
            )
        )

    model.client = SimpleNamespace(models=SimpleNamespace(generate_content=respond))
    with pytest.raises(ValueError, match="coverage"):
        model.score(request)


def test_normalized_x_and_bluesky_share_classification_and_rendering(conn):
    uid, _ = setup(conn)
    conn.execute(
        """INSERT INTO social_posts VALUES
        ('x','123','author-x','Human on X','Another original compiler finding','https://x.com/i/status/123',
         NULL,?,NULL,NULL,NULL,'',0,'[]',NULL,NULL)""",
        (int(time.time()),),
    )
    scorer = FakeScorer()
    worker.run_cycle(conn, scorer)
    assert {p["platform"] for p in digests.read(conn, uid)} == {"bluesky", "x"}
    assert {p.platform for p in scorer.requests[0].candidates} == {"bluesky", "x"}
    conn.execute("UPDATE social_posts SET deleted=1 WHERE platform='x' AND native_id='123'")
    assert {p["platform"] for p in digests.read(conn, uid)} == {"bluesky"}


def test_note_understanding_updates_private_profile_without_copying_job_payload(conn):
    uid, _ = setup(conn)
    public_before = profiles.markdown(conn, uid)
    private_note = "PRIVATE_SOURCE_ONLY with detailed compiler ideas"
    notes.import_note(conn, uid, "Source", private_note)
    job = conn.execute("SELECT * FROM feedback_jobs WHERE source_chunk_id IS NOT NULL").fetchone()
    assert job["kind"] == "private" and job["feedback"] == ""
    gemma = FakeGemma()
    jobs.process_one(conn, gemma)
    assert private_note in profiles.markdown(conn, uid, "private")
    assert profiles.markdown(conn, uid) == public_before


def test_profile_update_during_scoring_discards_inflight_result(conn):
    uid, _ = setup(conn)

    class ChangedProfile(FakeScorer):
        def score(self, request):
            profiles.save(conn, uid, "public", "# New intent", profiles.head(conn, uid)["id"])
            return super().score(request)

    assert worker.score_user(conn, ChangedProfile(), DID) == 0
    assert not conn.execute("SELECT 1 FROM assessments_v2").fetchone()


def test_note_removal_during_inference_cannot_reintroduce_private_profile_data(conn):
    uid, _ = setup(conn)
    note = notes.import_note(conn, uid, "Remove while processing", "private original")

    class Removed(FakeGemma):
        def update_profile(self, current, feedback):
            notes.delete_note(conn, uid, note)
            return super().update_profile(current, feedback)

    jobs.process_one(conn, Removed())
    assert profiles.head(conn, uid, "private") is None


def test_refresh_race_preserves_new_settings_request(conn):
    uid, _ = setup(conn)
    notes.import_note(conn, uid, "Source", "Original note")
    worker.score_user(conn, FakeScorer(), DID)

    class ChangedDuringMatching(FakeGemma):
        def match(self, *args):
            conn.execute("UPDATE reader_settings SET version=version+1,refresh_requested=1 WHERE user_id=?", (uid,))
            return super().match(*args)

    run_id = digests.build(conn, uid, worker.scorer_version())
    assert run_id
    digests.match_notes(conn, uid, run_id, ChangedDuringMatching())
    assert digests.config_for(conn, uid)["refresh_requested"] == 1


def test_digest_is_ready_before_private_matching_runs(conn):
    uid, _ = setup(conn)
    notes.import_note(conn, uid, "Source", "Original note")
    states = []

    class Observing(FakeGemma):
        def match(self, *args):
            states.append(conn.execute("SELECT value FROM kv WHERE key=?", ("curation-state:" + uid,)).fetchone()[0])
            return super().match(*args)

    worker.run_cycle(conn, FakeScorer(), Observing())
    assert states == ["ready"]
    assert digests.read(conn, uid)[0]["notes"]
    timing = json.loads(conn.execute("SELECT value FROM kv WHERE key=?", ("curation-timing:" + uid,)).fetchone()[0])
    assert timing["scored"] == 1 and timing["notes_matched"] == 1
    assert {"candidates_s", "scoring_s", "digest_s", "time_to_ready_s", "notes_s", "total_s"} <= set(timing)


def test_readers_are_curated_in_parallel_connections(conn):
    uid, _ = setup(conn)
    other = "did:plc:other"
    add_user(conn, other, "Compiler evaluations")
    other_uid = profiles.bootstrap(conn, other)
    worker.run_cycle(conn, FakeScorer())
    assert digests.latest(conn, uid) and digests.latest(conn, other_uid)


def test_failed_reader_does_not_block_others(conn):
    uid, _ = setup(conn)
    add_user(conn, "did:plc:other", "Compiler evaluations")
    other_uid = profiles.bootstrap(conn, "did:plc:other")

    class FailsForOther(FakeScorer):
        def score(self, request):
            if "Compiler evaluations" in request.explicit_profile:
                raise RuntimeError("provider down")
            return super().score(request)

    worker.run_cycle(conn, FailsForOther())
    assert digests.latest(conn, uid)
    assert conn.execute("SELECT value FROM kv WHERE key=?", ("curation-state:" + other_uid,)).fetchone()[0] == "failed"


def test_worker_wakes_for_new_requests_but_not_for_ones_it_already_saw(conn):
    uid, _ = setup(conn)
    worker.run_cycle(conn, FakeScorer())
    seen = worker.pending_work(conn)
    started = time.monotonic()
    worker.wait_for_work(conn, seen, timeout_s=0.3, poll_s=0.05)
    assert time.monotonic() - started >= 0.3
    conn.execute("UPDATE reader_settings SET refresh_requested=1,version=version+1 WHERE user_id=?", (uid,))
    started = time.monotonic()
    worker.wait_for_work(conn, seen, timeout_s=5, poll_s=0.05)
    assert time.monotonic() - started < 1
    stuck = worker.pending_work(conn)
    started = time.monotonic()
    worker.wait_for_work(conn, stuck, timeout_s=0.3, poll_s=0.05)
    assert time.monotonic() - started >= 0.3


def test_recall_finds_followed_and_topic_posts_beyond_recent_firehose_slice(conn):
    from curator_curation.candidates import public_candidates

    setup(conn)
    older = insert_post(conn, "did:plc:followed", "old", "Yesterday's specialist discussion", age_s=3600)
    conn.execute("INSERT INTO follows VALUES (?,?)", (DID, "did:plc:followed"))
    conn.execute(
        """INSERT INTO social_posts VALUES ('x','older','author','Human','Unique compiler topic',
        'https://x.com/i/status/older',NULL,?,NULL,NULL,NULL,'',0,'[]',NULL,NULL)""",
        (int(time.time()) - 7200,),
    )
    for i in range(10):
        insert_post(conn, "did:plc:noise", str(i), "Unrelated latest firehose message")
    keys = {p.key for p in public_candidates(conn, DID, ["Unique compiler"], 6)}
    assert "bluesky:" + older in keys
    assert "x:older" in keys


def test_baseline_scoring_runs_batches_concurrently(conn, monkeypatch):
    import threading

    from curator_core.config import clear_settings

    monkeypatch.setenv("SCORE_BATCH_SIZE", "1")
    monkeypatch.setenv("SCORE_CONCURRENCY", "2")
    clear_settings()
    add_user(conn, DID)
    uid = profiles.bootstrap(conn, DID)
    for n in range(2):
        insert_post(conn, "did:plc:human", str(n), f"Original source post {n}")
    barrier = threading.Barrier(2, timeout=5)

    class ParallelScorer(FakeScorer):
        def score(self, request):
            barrier.wait()
            return super().score(request)

    scorer = ParallelScorer()
    assert profiles.head(conn, uid) is None
    assert worker.score_user(conn, scorer, DID) == 2
    assert len(scorer.requests) == 2
    assert all("without assuming" in request.explicit_profile for request in scorer.requests)
    assert conn.execute("SELECT count(*) FROM assessments_v2 WHERE user_id=?", (uid,)).fetchone()[0] == 2
    clear_settings()


def test_scoring_attaches_post_images_and_reader_shows_media(conn, monkeypatch):
    import httpx
    from curator_core.media import bluesky_image
    from curator_curation import media

    uid, _ = setup(conn)
    author, cid = "did:plc:artist", "bafkreigh2akiscaildcqabsyg3dfr6chu3fgpregiymsck7e7aqa4s52zy"
    items = [
        {"type": "image", "thumb": bluesky_image(author, cid), "full": bluesky_image(author, cid), "alt": "IR graph"},
        {"type": "link", "url": "https://example.com/paper", "title": "Paper", "description": "", "thumb": None},
    ]
    insert_post(conn, author, "pic", "Compiler pass visualized", media=json.dumps(items))
    requested = []

    def handle(request):
        requested.append(request.url.host)
        return httpx.Response(200, content=b"\xff\xd8jpeg", headers={"content-type": "image/jpeg"})

    original = httpx.Client
    monkeypatch.setattr(media.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    scorer = FakeScorer()
    worker.run_cycle(conn, scorer)
    request = scorer.requests[0]
    candidate = next(p for p in request.candidates if p.text == "Compiler pass visualized")
    image_ref = candidate.media[0]["image_ref"]
    assert candidate.media[0]["alt"] == "IR graph"
    assert candidate.media[1] == {"type": "link", "url": "https://example.com/paper", "title": "Paper"}
    assert [(i.ref, i.data) for i in request.images] == [(image_ref, b"\xff\xd8jpeg")]
    assert requested == ["cdn.bsky.app"]
    parts = request.contents()
    assert parts[-2].text == f"image_ref {image_ref}:"
    assert parts[-1].inline_data.mime_type == "image/jpeg"
    shown = next(p for p in digests.read(conn, uid) if p["text"] == "Compiler pass visualized")
    assert json.loads(shown["media"]) == {
        "images": [{"url": items[0]["thumb"], "full_url": items[0]["full"], "alt": "IR graph"}],
        "external": {"url": "https://example.com/paper", "title": "Paper", "description": "", "image": None},
    }


def test_image_fetch_rejects_foreign_hosts_non_images_and_oversize(conn, monkeypatch):
    import httpx
    from curator_curation import media

    def handle(request):
        if request.url.path.endswith("big.jpg"):
            return httpx.Response(200, content=b"x" * 3_000_000, headers={"content-type": "image/jpeg"})
        return httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"})

    original = httpx.Client
    monkeypatch.setattr(media.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    wanted = {
        "a": ("bluesky", "https://evil.example/a.jpg"),
        "b": ("bluesky", "https://cdn.bsky.app/page.html"),
        "c": ("bluesky", "https://cdn.bsky.app/big.jpg"),
    }
    assert media.fetch_images(wanted) == ()


def decision_scorer(create):
    from curator_curation.llm.gemini import GeminiDecisionScorer

    scorer = object.__new__(GeminiDecisionScorer)
    scorer.model, scorer.timeout_s, scorer.concurrency, scorer.limiter = "gemini-decision-1", 30, 4, None
    scorer.client = SimpleNamespace(decisions=SimpleNamespace(create=create))
    return scorer


def decision_answers(grade=3.0, want=0.9, substance=2.0, opportunity=1.0):
    return {
        "grade": SimpleNamespace(type="score", score=grade),
        "want": SimpleNamespace(type="noul", noul=want),
        "substance": SimpleNamespace(type="score", score=substance),
        "opportunity": SimpleNamespace(type="score", score=opportunity),
    }


def test_decision_scorer_asks_once_per_post_with_only_its_images():
    from curator_curation.llm.gemini import DECISION_QUESTIONS, ScoringImage

    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        text = kwargs["input"][0]["text"]
        return SimpleNamespace(answers=decision_answers(want=0.9 if "x:one" in text else 0.1))

    request = PublicScoringRequest(
        "Public preferences",
        (
            PublicCandidate("x:one", "x", "Original text", "", False, False, ({"type": "image", "image_ref": "p0m0"},)),
            PublicCandidate("x:two", "x", "Other text", "", False, False),
        ),
        (ScoringImage("p0m0", "image/png", b"original-image"),),
    )
    result = decision_scorer(create).score(request)
    by_key = {item.post_key: item for item in result.items}
    assert [i.post_key for i in result.items] == ["x:one", "x:two"]
    assert by_key["x:one"].decision == "include" and by_key["x:two"].decision == "exclude"
    assert by_key["x:one"].relevance == 1.0 and by_key["x:one"].substance == 0.5 and by_key["x:one"].opportunity == 0.25
    assert len(calls) == 2 and all(c["questions"] == DECISION_QUESTIONS for c in calls)
    first = next(c for c in calls if "x:one" in c["input"][0]["text"])
    second = next(c for c in calls if "x:two" in c["input"][0]["text"])
    assert "Public preferences" in first["input"][0]["text"] and "Other text" not in first["input"][0]["text"]
    assert first["input"][2] == {"type": "image", "mime_type": "image/png", "data": "b3JpZ2luYWwtaW1hZ2U="}
    assert len(second["input"]) == 1


def test_decision_scorer_rejects_missing_or_invalid_answers():
    request = PublicScoringRequest("Public", (PublicCandidate("x:one", "x", "Text", "", False, False),))
    with pytest.raises(ValueError, match="coverage"):
        decision_scorer(lambda **kwargs: SimpleNamespace(answers={})).score(request)
    with pytest.raises(ValueError):
        decision_scorer(lambda **kwargs: SimpleNamespace(answers=decision_answers(grade=5))).score(request)
    wrong_type = decision_answers()
    wrong_type["want"] = SimpleNamespace(type="score", score=1.0)
    with pytest.raises(ValueError):
        decision_scorer(lambda **kwargs: SimpleNamespace(answers=wrong_type)).score(request)


def test_failed_batches_return_their_scoring_budget(conn):
    uid, _ = setup(conn)

    class Down:
        def score(self, request):
            raise RuntimeError("provider down")

    worker.run_cycle(conn, Down())
    assert int(conn.execute("SELECT value FROM kv WHERE key=?", (worker.budget_key(uid),)).fetchone()[0]) == 0


def test_discovery_prefers_posts_gaining_engagement_fastest(conn):
    from curator_curation.candidates import public_candidates

    setup(conn)
    for i in range(10):
        insert_post(conn, "did:plc:noise", str(i), "Unrelated latest firehose message")
    fast = insert_post(conn, "did:plc:a", "fast", "Fresh post taking off", age_s=1800, likes=40)
    slow = insert_post(conn, "did:plc:b", "slow", "Old post that drifted up", age_s=20 * 3600, likes=60)
    insert_post(conn, "did:plc:c", "reply", "Busy reply", age_s=600, likes=90, reply_parent="at://x/y/z")
    keys = [p.key for p in public_candidates(conn, DID, [], 6)]
    assert keys[:2] == ["bluesky:" + fast, "bluesky:" + slow]


def test_recall_includes_posts_liked_by_followed_accounts(conn):
    from curator_curation.candidates import public_candidates

    setup(conn)
    conn.execute("INSERT INTO follows VALUES (?,?)", (DID, "did:plc:friend"))
    for i in range(10):
        insert_post(conn, "did:plc:noise", str(i), "Unrelated latest firehose message")
    liked = insert_post(conn, "did:plc:stranger", "liked", "Older post a friend liked", age_s=7200)
    once = insert_post(conn, "did:plc:other", "once", "Post liked by a stranger", age_s=7200)
    now = int(time.time())
    conn.execute("INSERT INTO followed_engagement VALUES ('did:plc:friend',?,'like',?)", (liked, now))
    conn.execute("INSERT INTO followed_engagement VALUES ('did:plc:nobody',?,'like',?)", (once, now))
    keys = [p.key for p in public_candidates(conn, DID, [], 3)]
    assert keys[0] == "bluesky:" + liked
    assert "bluesky:" + once not in keys


def test_pending_rebuild_keeps_the_last_selection_instead_of_raw_candidates(conn):
    uid, key = setup(conn)
    worker.run_cycle(conn, FakeScorer())
    assert [p["key"] for p in digests.read(conn, uid)] == [key]
    for i in range(5):
        insert_post(conn, f"did:plc:raw{i}", str(i), f"Unscored keyword match {i}")
    conn.execute("UPDATE reader_settings SET refresh_requested=1,version=version+1 WHERE user_id=?", (uid,))
    body = profiles.markdown(conn, uid) + "\n- More: something new"
    profiles.save(conn, uid, "public", body, profiles.head(conn, uid)["id"])
    assert [p["key"] for p in digests.read(conn, uid)] == [key]
