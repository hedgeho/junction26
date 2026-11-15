import time

from curator_core.testing import add_user, insert_post
from curator_curation import digests, profiles, worker
from curator_curation.llm.schemas import Assessment, Assessments

DID = "did:plc:partial"


def include(key):
    return Assessment(post_key=key, decision="include", relevance=0.9, substance=0.8, opportunity=0.7)


class SkipsFirst:
    """Returns every assessment except the first post's, like a model that drops one item."""

    def __init__(self):
        self.requests = []

    def score(self, request):
        self.requests.append(request)
        return Assessments(items=[include(p.post_key) for p in request.candidates[1:]])


class Fixed:
    def __init__(self, decision="include"):
        self.decision = decision

    def score(self, request):
        return Assessments(
            items=[
                Assessment(post_key=p.post_key, decision=self.decision, relevance=0.9, substance=0.8, opportunity=0.7)
                for p in request.candidates
            ]
        )


def setup(conn, n=3):
    add_user(conn, DID, "Practical compilers, not launch hype")
    uid = profiles.bootstrap(conn, DID)
    keys = [
        "bluesky:" + insert_post(conn, f"did:plc:author{i}", str(i), f"Compiler evaluation number {i} with results")
        for i in range(n)
    ]
    return uid, keys


def stored(conn, uid):
    return {r[0] for r in conn.execute("SELECT post_key FROM assessments_v2 WHERE user_id=?", (uid,))}


def test_partial_batch_keeps_returned_assessments_and_refunds_the_rest(conn):
    uid, keys = setup(conn)
    scorer = SkipsFirst()
    timing = {}
    worker.score_user(conn, scorer, DID, timing)
    scored = stored(conn, uid)
    assert len(scored) == len(keys) - 1 and scored < set(keys)
    assert timing["skipped"] == 1 and timing["batch_errors"] == 0
    assert int(conn.execute("SELECT value FROM kv WHERE key=?", (worker.budget_key(uid),)).fetchone()[0]) == 2
    worker.score_user(conn, Fixed(), DID)
    assert stored(conn, uid) == set(keys)


def test_empty_digest_is_rebuilt_after_new_inclusions(conn):
    uid, keys = setup(conn)
    worker.run_cycle(conn, Fixed("exclude"))
    last = digests.latest(conn, uid)
    assert digests.read(conn, uid) == []
    conn.execute("UPDATE assessments_v2 SET decision='include', created_at=? WHERE user_id=?", (int(time.time()), uid))
    assert worker.stale_empty_digest(conn, uid, last)
    worker.run_cycle(conn, Fixed())
    assert digests.latest(conn, uid)["id"] != last["id"]
    assert {p["key"] for p in digests.read(conn, uid)} == set(keys)


def test_empty_digest_without_new_inclusions_is_kept(conn):
    uid, _ = setup(conn)
    worker.run_cycle(conn, Fixed("exclude"))
    last = digests.latest(conn, uid)
    worker.run_cycle(conn, Fixed("exclude"))
    assert digests.latest(conn, uid)["id"] == last["id"]
