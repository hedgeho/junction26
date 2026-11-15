import itertools
import time

from curator_core import text
from curator_core.sources import get_post
from curator_core.testing import add_user, insert_post
from curator_curation import digests, funnel, profiles, triage, worker
from curator_curation.llm.schemas import Assessment, Assessments

DID = "did:plc:reader"
FLAGS_CLEAN = dict.fromkeys(triage.FLAGS, 0.0)


class TopicScorer:
    def score(self, request):
        return Assessments(
            items=[
                Assessment(
                    post_key=p.post_key,
                    decision="exclude" if "gardening" in p.text else "include",
                    relevance=0.1 if "gardening" in p.text else 0.9,
                    substance=0.8,
                    opportunity=0.7,
                )
                for p in request.candidates
            ]
        )


def post(conn, author, rkey, body):
    return "bluesky:" + insert_post(conn, author, rkey, body, text_sig=text.signature(body))


def seed(conn):
    add_user(conn, DID, "Compilers")
    uid = profiles.bootstrap(conn, DID)
    keys = {
        "good": [post(conn, f"did:plc:good{i}", "1", f"Compiler benchmark number {i} with raw data") for i in range(3)],
        "off": post(conn, "did:plc:garden", "1", "My weekend gardening notes and tomato photos"),
        "rage": post(conn, "did:plc:angry", "1", "Compiler people are ruining everything, outrageous"),
        "dupes": [post(conn, f"did:plc:dup{i}", "1", "The same compiler release note shared twice") for i in range(2)],
    }
    for i in range(5):
        post(conn, f"did:plc:bot{i}", "1", "Buy compiler coins now before the launch")
    posts = [get_post(conn, k) for k in [*keys["good"], keys["off"], keys["rage"], *keys["dupes"]]]
    flags = {p.key: dict(FLAGS_CLEAN) for p in posts}
    flags[keys["rage"]] |= {"ragebait": 0.95, "clickbait": 0.8}
    triage.store(conn, posts, flags, "test")
    worker.run_cycle(conn, TopicScorer())
    return uid, keys


def by_key(result):
    return {s["key"]: s for s in result["stages"]}


def drops(stage):
    return {d["key"]: d["count"] for d in stage["drops"]}


def test_every_candidate_is_counted_once_down_to_the_delivered_feed(conn):
    uid, _ = seed(conn)
    result = funnel.compute(conn, uid, DID)
    stages = [s for s in result["stages"] if s["key"] != "firehose"]
    for stage, following in itertools.pairwise(stages):
        assert stage["count"] - sum(d["count"] for d in stage["drops"]) == following["count"], stage["key"]
    assert stages[-1]["count"] == len(digests.read(conn, uid)) == 4


def test_funnel_splits_drops_by_reason_and_content_flag(conn):
    uid, _ = seed(conn)
    stages = by_key(funnel.compute(conn, uid, DID))
    assert drops(stages["candidates"])["campaign"] == 5
    assert drops(stages["scored"])["off_topic"] == 1
    content = drops(stages["relevant"])
    # A post over two thresholds counts once, under the flag it clears by more.
    assert content["ragebait"] == 1 and content["clickbait"] == 0 and content["spam_promo"] == 0
    assert drops(stages["clean"]) == {"duplicate": 1}
    assert "firehose" not in stages


def test_funnel_starts_at_the_firehose_when_ingest_has_counted_it(conn):
    uid, _ = seed(conn)
    hour = int(time.time()) // 3600
    stats = [(hour - 30, 999, 999), (hour - 3, 5, 5), (hour - 2, 100, 60), (hour - 1, 200, 120), (hour, 7, 7)]
    conn.executemany("INSERT INTO firehose_stats VALUES (?,?,?)", stats)
    first = funnel.compute(conn, uid, DID)["stages"][0]
    # Only the complete hours inside the window count; their rate is scaled to a day.
    assert first["key"] == "firehose" and first["count"] == round(305 * 24 / 3)
    assert drops(first) == {"language": round(185 * 24 / 3)}


def test_firehose_is_omitted_until_one_full_hour_is_counted(conn):
    uid, _ = seed(conn)
    hour = int(time.time()) // 3600
    conn.executemany("INSERT INTO firehose_stats VALUES (?,?,?)", [(hour - 1, 50, 10), (hour, 40, 5)])
    assert funnel.compute(conn, uid, DID)["stages"][0]["key"] == "window"


def test_firehose_label_says_when_it_is_an_estimate(conn):
    uid, _ = seed(conn)
    hour = int(time.time()) // 3600
    conn.executemany("INSERT INTO firehose_stats VALUES (?,?,?)", [(hour - h, 10, 1) for h in range(4)])
    first = funnel.compute(conn, uid, DID)["stages"][0]
    assert first["label"] == "Bluesky firehose per day, measured over 2 h" and first["count"] == 240


def test_daily_limit_and_unscored_candidates_have_their_own_buckets(conn):
    uid, _ = seed(conn)
    conn.execute("UPDATE reader_settings SET daily_limit=2, refresh_requested=1 WHERE user_id=?", (uid,))
    worker.run_cycle(conn, TopicScorer())
    post(conn, "did:plc:late", "1", "A compiler post that arrived after scoring")
    stages = by_key(funnel.compute(conn, uid, DID))
    assert stages["delivered"]["count"] == 2
    assert drops(stages["clean"])["limit"] == 2
    assert drops(stages["candidates"])["pending"] == 1
