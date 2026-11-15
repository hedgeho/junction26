import time

from curator_core import db
from curator_core.config import clear_settings
from curator_core.testing import add_user, insert_post
from curator_curation import digests, profiles, worker
from curator_curation.llm.schemas import Assessment, Assessments

from .test_triage import FakeTriager

DID = "did:plc:reader"
FRIEND = "did:plc:friend"


class ScriptedScorer:
    """Assessment by the first matching marker word in the post text."""

    def __init__(self, script):
        self.script = script
        self.scored = []

    def score(self, request):
        items = []
        for c in request.candidates:
            self.scored.append(c.text)
            decision, relevance, substance = next(v for k, v in self.script.items() if k in c.text)
            items.append(
                Assessment(
                    post_key=c.post_key, decision=decision, relevance=relevance, substance=substance, opportunity=0.2
                )
            )
        return Assessments(items=items)


def reader(conn):
    add_user(conn, DID, "Compilers", follows=[FRIEND])
    return profiles.bootstrap(conn, DID)


def strangers(conn, n, marker="STRONG"):
    for i in range(n):
        insert_post(conn, f"did:plc:s{i}", "1", f"{marker} post {i} about a distinct compiler optimisation", age_s=i)


def test_followed_authors_get_reserved_slots(conn):
    uid = reader(conn)
    strangers(conn, 12)
    insert_post(conn, FRIEND, "1", "WEAK friend's modest note on a compiler bug")
    worker.run_cycle(conn, ScriptedScorer({"STRONG": ("include", 0.95, 0.9), "WEAK": ("include", 0.45, 0.3)}))
    texts = [p["text"] for p in digests.read(conn, uid)]
    assert len(texts) == 10
    assert any("friend's" in t for t in texts)


def test_serendipity_share_takes_clean_substantive_nearby_posts(conn):
    uid = reader(conn)
    strangers(conn, 12)
    insert_post(conn, "did:plc:n1", "1", "NEARBY deep dive into GPU scheduling with measurements")
    insert_post(conn, "did:plc:n2", "1", "FLAGGED nearby hot take on GPU vendors")
    insert_post(conn, "did:plc:n3", "1", "THIN nearby one-liner about GPUs")
    scorer = ScriptedScorer(
        {
            "STRONG": ("include", 0.95, 0.9),
            "NEARBY": ("exclude", 0.6, 0.9),
            "FLAGGED": ("exclude", 0.6, 0.9),
            "THIN": ("exclude", 0.6, 0.2),
        }
    )
    worker.run_cycle(conn, scorer, triager=FakeTriager({"FLAGGED": {"ragebait": 0.8}}))
    texts = [p["text"] for p in digests.read(conn, uid)]
    assert len(texts) == 10
    assert sum("NEARBY" in t for t in texts) == 1
    assert not any("FLAGGED" in t or "THIN" in t for t in texts)
    assert texts[-1].startswith("NEARBY")


def test_decision_gate_uses_its_own_relevance_floor(conn):
    uid = reader(conn)
    insert_post(conn, "did:plc:a", "1", "MID a relevant but not top compiler post")
    worker.run_cycle(conn, ScriptedScorer({"MID": ("include", 0.45, 0.5)}))
    assert len(digests.read(conn, uid)) == 1


def test_current_digest_keeps_scoring_ahead_within_pacing(conn, monkeypatch):
    uid = reader(conn)
    insert_post(conn, "did:plc:a", "1", "STRONG first compiler post")
    scorer = ScriptedScorer({"STRONG": ("include", 0.95, 0.9)})
    worker.run_cycle(conn, scorer)
    run = digests.latest(conn, uid)["id"]
    insert_post(conn, "did:plc:b", "1", "STRONG second compiler post")
    noon = time.time() // 86400 * 86400 + 43200
    monkeypatch.setattr(worker.time, "time", lambda: noon)
    worker.run_cycle(conn, scorer)
    assert scorer.scored[-1] == "STRONG second compiler post"
    assert digests.latest(conn, uid)["id"] == run

    insert_post(conn, "did:plc:c", "1", "STRONG third compiler post")
    worker.run_cycle(conn, scorer)
    assert "STRONG third compiler post" not in scorer.scored  # within the interval

    db.kv_set(conn, "background-scored:" + uid, "0")
    db.kv_set(conn, worker.budget_key(uid), str(10**6))
    worker.run_cycle(conn, scorer)
    assert "STRONG third compiler post" not in scorer.scored  # over the paced allowance


def test_background_scoring_covers_a_full_batch_each_interval(conn, monkeypatch):
    monkeypatch.setenv("BACKGROUND_SCORE_SIZE", "40")
    clear_settings()
    uid = reader(conn)
    insert_post(conn, "did:plc:a", "1", "STRONG first compiler post")
    scorer = ScriptedScorer({"STRONG": ("include", 0.95, 0.9)})
    worker.run_cycle(conn, scorer)
    strangers(conn, 50)
    noon = time.time() // 86400 * 86400 + 43200
    monkeypatch.setattr(worker.time, "time", lambda: noon)
    db.kv_set(conn, "background-scored:" + uid, "0")
    worker.run_cycle(conn, scorer)
    assert len(scorer.scored) == 1 + 40
