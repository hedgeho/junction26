from types import SimpleNamespace

import pytest
from curator_core.testing import add_user, insert_post
from curator_curation import digests, notes, profiles, triage, worker
from curator_curation.llm.gemini import FLAG_CRITERIA, GeminiDecisionTriager, PublicCandidate

from .test_quiet_curation import FakeScorer

CLEAN = {flag: 0.05 for flag in FLAG_CRITERIA}


class FakeTriager:
    model = "gemini-decision-1"

    def __init__(self, flags_by_word=None, fail_on=None):
        self.flags_by_word = flags_by_word or {}
        self.fail_on = fail_on
        self.seen = []

    def flags(self, candidate):
        self.seen.append(candidate)
        if self.fail_on and self.fail_on in candidate.text:
            raise RuntimeError("provider down")
        out = dict(CLEAN)
        for word, flags in self.flags_by_word.items():
            if word in candidate.text:
                out |= flags
        return out


def reader(conn, did="did:plc:reader"):
    add_user(conn, did, "Compilers")
    return profiles.bootstrap(conn, did)


def test_hidden_flags_remove_posts_and_soft_flags_downrank(conn):
    uid = reader(conn)
    insert_post(conn, "did:plc:a", "1", "SPAMWORD buy followers now, a compiler deal", age_s=10)
    insert_post(conn, "did:plc:b", "2", "MISWORD compilers secretly run on crystals, study says", age_s=0)
    insert_post(conn, "did:plc:c", "3", "A careful compiler benchmark with reproducible data", age_s=20)
    triager = FakeTriager({"SPAMWORD": {"spam_promo": 0.9}, "MISWORD": {"misinformation": 0.95}})
    worker.run_cycle(conn, FakeScorer(), triager=triager)
    texts = [p["text"] for p in digests.read(conn, uid)]
    assert not any("SPAMWORD" in t for t in texts)
    assert texts[0].startswith("A careful") and texts[1].startswith("MISWORD")


def test_flags_are_shared_across_readers_and_assessed_once(conn):
    reader(conn)
    reader(conn, "did:plc:second")
    insert_post(conn, "did:plc:a", "1", "A careful compiler benchmark with reproducible data")
    triager = FakeTriager()
    worker.run_cycle(conn, FakeScorer(), triager=triager)
    assert len(triager.seen) in (1, 2)  # concurrent readers may race on a brand-new post
    before = len(triager.seen)
    conn.execute("UPDATE reader_settings SET refresh_requested=1,version=version+1")
    worker.run_cycle(conn, FakeScorer(), triager=triager)
    assert len(triager.seen) == before
    assert conn.execute("SELECT count(*) FROM post_flags").fetchone()[0] == 1


def test_triage_failure_keeps_posts_eligible_and_retries(conn):
    uid = reader(conn)
    insert_post(conn, "did:plc:a", "1", "FAILWORD but a careful compiler benchmark")
    triager = FakeTriager(fail_on="FAILWORD")
    worker.run_cycle(conn, FakeScorer(), triager=triager)
    assert len(digests.read(conn, uid)) == 1
    conn.execute("UPDATE reader_settings SET refresh_requested=1,version=version+1")
    worker.run_cycle(conn, FakeScorer(), triager=triager)
    assert len(triager.seen) == 2


def test_triage_sees_only_public_post_fields(conn):
    uid = reader(conn)
    notes.import_note(conn, uid, "Private", "PRIVATE_SENTINEL note text")
    insert_post(conn, "did:plc:a", "1", "A careful compiler benchmark with reproducible data")
    triager = FakeTriager()
    worker.run_cycle(conn, FakeScorer(), triager=triager)
    assert triager.seen and all(isinstance(c, PublicCandidate) for c in triager.seen)
    assert "PRIVATE_SENTINEL" not in repr(triager.seen)
    assert "Compilers" not in repr(triager.seen)


def test_hidden_posts_are_not_shown_as_provisional_candidates(conn):
    uid = reader(conn)
    uri = insert_post(conn, "did:plc:a", "1", "Clickbait you won't believe what this compiler did")
    insert_post(conn, "did:plc:b", "2", "A careful compiler benchmark with reproducible data")
    from curator_core.sources import get_post

    post = get_post(conn, "bluesky:" + uri)
    triage.store(conn, [post], {post.key: CLEAN | {"clickbait": 0.95}}, "gemini-decision-1")
    texts = [p["text"] for p in digests.read(conn, uid)]
    assert texts == ["A careful compiler benchmark with reproducible data"]


def test_policy_thresholds():
    assert not triage.hidden(None) and triage.penalty(None) == 1
    assert triage.hidden(CLEAN | {"spam_promo": 0.4})
    assert not triage.hidden(CLEAN | {"misinformation": 0.99})
    assert triage.penalty(CLEAN | {"misinformation": 0.99, "low_effort": 0.9}) == 0.25


def test_decision_triager_asks_typed_flags_and_validates_answers():
    seen = {}

    def create(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(answers={f: SimpleNamespace(type="noul", noul=0.2) for f in kwargs["questions"]})

    triager = object.__new__(GeminiDecisionTriager)
    triager.model, triager.timeout_s, triager.limiter = "gemini-decision-1", 30, None
    triager.client = SimpleNamespace(decisions=SimpleNamespace(create=create))
    candidate = PublicCandidate("x:1", "x", "Original text", "", False, True, ({"type": "link", "title": "Headline"},))
    assert triager.flags(candidate) == {f: 0.2 for f in FLAG_CRITERIA}
    assert set(seen["questions"]) == set(FLAG_CRITERIA)
    assert all(q["type"] == "noul" for q in seen["questions"].values())
    assert '"link_title": "Headline"' in seen["input"] and '"is_quote": true' in seen["input"]

    triager.client.decisions.create = lambda **kwargs: SimpleNamespace(answers={})
    with pytest.raises(ValueError):
        triager.flags(candidate)


def test_decision_questions_use_only_fields_the_gemini_api_accepts():
    from curator_curation.llm.gemini import DECISION_QUESTIONS
    from google.genai._gaos.types.decisions.noulcriteria import NoulCriteriaParam
    from google.genai._gaos.types.decisions.noulquestion import NoulQuestionParam
    from google.genai._gaos.types.decisions.scorequestion import ScoreQuestionParam

    seen = {}

    def create(**kwargs):
        seen.update(kwargs["questions"])
        return SimpleNamespace(answers={f: SimpleNamespace(type="noul", noul=0.2) for f in kwargs["questions"]})

    triager = object.__new__(GeminiDecisionTriager)
    triager.model, triager.timeout_s, triager.limiter = "gemini-decision-1", 30, None
    triager.client = SimpleNamespace(decisions=SimpleNamespace(create=create))
    triager.flags(PublicCandidate("x:1", "x", "Text", "", False, False))
    allowed = {"noul": NoulQuestionParam, "score": ScoreQuestionParam}
    for question in [*DECISION_QUESTIONS.values(), *seen.values()]:
        assert set(question) <= set(allowed[question["type"]].__annotations__)
        if question["type"] == "noul":
            assert set(question["criteria"]) <= set(NoulCriteriaParam.__annotations__)


def test_triage_skips_posts_the_scoring_budget_will_not_reach(conn, monkeypatch):
    from curator_core.config import clear_settings

    monkeypatch.setenv("DAILY_SCORING_CAP", "1")
    monkeypatch.setenv("SCORE_BATCH_SIZE", "1")
    clear_settings()
    reader(conn)
    for i in range(3):
        insert_post(conn, f"did:plc:a{i}", "1", f"Distinct careful compiler benchmark number {i}")
    triager = FakeTriager()
    worker.run_cycle(conn, FakeScorer(), triager=triager)
    assert len(triager.seen) == 1
