from curator_core import db
from curator_core.testing import add_user
from curator_curation import profiles, seed
from curator_curation.llm.schemas import SeedProfile

DID = "did:plc:reader"
POSTS = [f"Spent the morning photographing warblers at the marsh, day {i}" for i in range(6)]


class FakeSeeder:
    def __init__(self, result=None, fail=False):
        self.result = result or SeedProfile(interests=["Bird photography"], search_terms=["warbler", "birding"])
        self.fail = fail
        self.seen = []

    def seed(self, texts):
        self.seen.append(texts)
        if self.fail:
            raise RuntimeError("SECRET_PROVIDER_PAYLOAD")
        return self.result


def test_reader_without_profile_is_seeded_from_own_posts(conn):
    add_user(conn, DID)
    uid = profiles.bootstrap(conn, DID)
    assert profiles.head(conn, uid) is None
    seeder = FakeSeeder()
    assert seed.maybe_seed(conn, DID, uid, seeder, fetch=lambda did: POSTS)
    head = profiles.head(conn, uid)
    assert head["origin"] == "seed-from-posts"
    assert "Bird photography" in profiles.markdown(conn, uid)
    assert seeder.seen == [POSTS]
    assert not seed.maybe_seed(conn, DID, uid, seeder, fetch=lambda did: POSTS)


def test_existing_profiles_and_thin_histories_are_left_alone(conn):
    add_user(conn, DID, "Compilers")
    uid = profiles.bootstrap(conn, DID)
    seeder = FakeSeeder()
    assert not seed.maybe_seed(conn, DID, uid, seeder, fetch=lambda did: POSTS)
    add_user(conn, "did:plc:new")
    new = profiles.bootstrap(conn, "did:plc:new")
    assert not seed.maybe_seed(conn, "did:plc:new", new, seeder, fetch=lambda did: POSTS[:2])
    assert seeder.seen == [] and profiles.head(conn, new) is None


def test_failures_and_empty_results_keep_the_neutral_baseline_and_retry_tomorrow(conn):
    add_user(conn, DID)
    uid = profiles.bootstrap(conn, DID)
    assert not seed.maybe_seed(conn, DID, uid, FakeSeeder(fail=True), fetch=lambda did: POSTS)
    assert profiles.head(conn, uid) is None
    db.kv_set(conn, "profile-seed:" + uid, "0")
    empty = FakeSeeder(SeedProfile(interests=[], search_terms=[]))
    assert not seed.maybe_seed(conn, DID, uid, empty, fetch=lambda did: POSTS)
    assert profiles.head(conn, uid) is None


def test_own_posts_skip_reposts_replies_and_other_authors(monkeypatch):
    import httpx

    feed = {
        "feed": [
            {"post": {"author": {"did": DID}, "record": {"text": "An original post about marsh warblers"}}},
            {
                "post": {"author": {"did": "did:plc:x"}, "record": {"text": "Someone else's post, reposted"}},
                "reason": {},
            },
            {"post": {"author": {"did": DID}, "record": {"text": "short"}}},
        ]
    }

    def handle(request):
        assert request.url.path == "/xrpc/app.bsky.feed.getAuthorFeed"
        assert request.url.params["filter"] == "posts_no_replies"
        return httpx.Response(200, json=feed)

    original = httpx.Client
    monkeypatch.setattr(seed.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    assert seed.own_posts(DID) == ["An original post about marsh warblers"]
