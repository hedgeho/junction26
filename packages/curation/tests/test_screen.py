import json

import pytest
from curator_core import media, text
from curator_core.config import clear_settings
from curator_core.sources import get_post
from curator_core.testing import add_user, insert_post
from curator_curation import digests, profiles, screen, worker

from .test_quiet_curation import FakeScorer

DID = "did:plc:reader"
BODY = "A careful compiler benchmark with reproducible results and raw data"


def post(conn, author, rkey, body=BODY, **cols):
    uri = insert_post(conn, author, rkey, body, text_sig=text.signature(body), **cols)
    return get_post(conn, "bluesky:" + uri)


@pytest.fixture
def labels_on(monkeypatch):
    monkeypatch.setenv("LABEL_CHECKS", "true")
    clear_settings()


def test_signature_ignores_case_punctuation_links_and_tags():
    a = text.signature("Big NEWS: compilers are fast now!! https://x.example/a #tech @bob.bsky.social")
    assert a == text.signature("big news compilers are fast now https://y.example/b #other")
    assert a != text.signature("Big news: compilers are slow now")
    assert text.signature("gm") is None


def test_copy_paste_campaign_is_dropped_even_from_followed_authors(conn):
    posts = [post(conn, f"did:plc:bot{i}", "1") for i in range(5)]
    unique = post(conn, "did:plc:human", "2", "An unrelated original thought about parser generators")
    kept, dropped = screen.screen(conn, [*posts, unique], {"did:plc:bot0"}, 10)
    assert [p.key for p in kept] == [unique.key]
    assert dropped["campaign"] == 5


def test_a_few_accounts_sharing_a_text_is_not_a_campaign(conn):
    posts = [post(conn, f"did:plc:friend{i}", "1") for i in range(4)]
    kept, _ = screen.screen(conn, posts, set(), 10)
    assert len(kept) == 4


def test_high_rate_accounts_are_dropped_unless_followed(conn, monkeypatch):
    monkeypatch.setenv("MAX_AUTHOR_POSTS_PER_DAY", "3")
    clear_settings()
    for i in range(4):
        last = post(conn, "did:plc:firehose", str(i), f"Post number {i} with plenty of distinct words here")
    kept, dropped = screen.screen(conn, [last], set(), 10)
    assert kept == [] and dropped["author_rate"] == 1
    kept, _ = screen.screen(conn, [last], {"did:plc:firehose"}, 10)
    assert kept == [last]


def test_tag_and_mention_stuffing_is_dropped(conn):
    tags = post(conn, "did:plc:a", "1", "Follow me " + " ".join(f"#tag{i}" for i in range(6)))
    mentions = post(conn, "did:plc:b", "2", "hey " + " ".join(f"@user{i}.bsky.social" for i in range(6)))
    fine = post(conn, "did:plc:c", "3", "Mail me at me@example.com about #compilers")
    captioned = post(
        conn,
        "did:plc:d",
        "4",
        "A Spotted Towhee with a full mouth and a busy background. "
        "#birds #birdphotography #photography #nature #wildlife #birding #BirdOfTheDay",
    )
    kept, dropped = screen.screen(conn, [tags, mentions, fine, captioned], set(), 10)
    assert kept == [fine, captioned] and dropped["tags"] == 2


def test_labels_drop_posts_and_authors_and_are_cached(conn, labels_on, monkeypatch):
    clean = post(conn, "did:plc:a", "1", "Clean original post about register allocation")
    spam = post(conn, "did:plc:b", "2", "Labeled post about register allocation tricks")
    author = post(conn, "did:plc:c", "3", "Author-labeled post about instruction selection")
    gone = post(conn, "did:plc:d", "4", "Taken down post about loop unrolling heuristics")
    calls = []

    def fetch(uris):
        calls.append(list(uris))
        views = {
            clean.native_id: [],
            spam.native_id: ["spam"],
            author.native_id: ["author:impersonation"],
        }
        return {u: views.get(u, [screen.UNAVAILABLE]) for u in uris}

    monkeypatch.setattr(screen, "fetch_labels", fetch)
    kept, dropped = screen.screen(conn, [clean, spam, author, gone], set(), 10)
    assert kept == [clean] and dropped["labels"] == 3
    screen.screen(conn, [clean, spam, author, gone], set(), 10)
    assert len(calls) == 1


def test_label_lookup_failure_fails_open_and_retries(conn, labels_on, monkeypatch):
    p = post(conn, "did:plc:a", "1")

    def broken(uris):
        raise RuntimeError("appview down")

    monkeypatch.setattr(screen, "fetch_labels", broken)
    kept, _ = screen.screen(conn, [p], set(), 10)
    assert kept == [p]
    assert screen.labels_of(conn, p.key) == []


def test_only_the_scoring_head_is_looked_up(conn, labels_on, monkeypatch):
    posts = [post(conn, f"did:plc:a{i}", str(i), f"Distinct post {i} about compiler passes and IR") for i in range(20)]
    seen = []
    monkeypatch.setattr(screen, "fetch_labels", lambda uris: seen.extend(uris) or {u: [] for u in uris})
    kept, _ = screen.screen(conn, posts, set(), 4)
    assert len(kept) == 4 and len(seen) == 5


def test_story_key_normalizes_tracking_and_www():
    def linked(url):
        return type("P", (), {"media": ({"type": "link", "url": url},)})()

    a = screen.story_key(linked("https://www.example.com/story/?utm_source=bsky&id=7"))
    assert a == screen.story_key(linked("http://example.com/story?id=7"))
    assert a != screen.story_key(linked("https://example.com/story?id=8"))


def test_digest_delivers_one_post_per_story_and_skips_labeled_posts(conn):
    add_user(conn, DID, "Compilers")
    uid = profiles.bootstrap(conn, DID)
    link = media.dumps([{"type": "link", "url": "https://news.example/compiler-release"}])
    post(conn, "did:plc:a", "1", "My take on the compiler release and its benchmarks", media=link)
    post(conn, "did:plc:b", "2", "Another take on the new compiler release numbers", media=link)
    labeled = post(conn, "did:plc:c", "3", "A labeled but otherwise relevant compiler post")
    conn.execute("INSERT INTO post_labels VALUES (?,?,strftime('%s','now'))", (labeled.key, json.dumps(["spam"])))
    worker.run_cycle(conn, FakeScorer())
    keys = [p["key"] for p in digests.read(conn, uid)]
    assert len(keys) == 1 and labeled.key not in keys
