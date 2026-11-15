import time

import pytest
from curator_core.sources import get_post
from curator_core.testing import insert_post
from curator_ingest.imports import import_posts


def source(platform="x", native_id="123"):
    return {
        "platform": platform,
        "native_id": native_id,
        "author_id": "human",
        "text": "Original text",
        "canonical_url": "https://x.com/i/status/123",
        "observed_at": int(time.time()),
    }


def test_public_pipeline_import_update_and_delete(conn):
    assert import_posts(conn, [source()]) == 1
    assert get_post(conn, "x:123").text == "Original text"
    item = source() | {"text": "Edited original"}
    import_posts(conn, [item])
    assert get_post(conn, "x:123").text == "Edited original"
    import_posts(conn, [item | {"deleted": True}])
    assert get_post(conn, "x:123") is None


def test_normalized_tombstone_overrides_legacy_bluesky(conn):
    uri = insert_post(conn, "did:plc:human", "1", "Legacy original")
    item = source("bluesky", uri) | {"deleted": True, "canonical_url": "https://bsky.app/profile/did:plc:human/post/1"}
    import_posts(conn, [item])
    assert get_post(conn, "bluesky:" + uri) is None


def test_bad_batch_never_partially_writes_or_injects_links(conn):
    with pytest.raises(ValueError):
        import_posts(conn, [source(), source() | {"canonical_url": "javascript:alert(1)"}])
    assert not conn.execute("SELECT 1 FROM social_posts").fetchone()


def test_jetstream_delete_invalidates_normalized_copy(conn):
    from curator_ingest.jetstream import Batch, flush

    uri = insert_post(conn, "did:plc:human", "1", "Original source")
    item = source("bluesky", uri) | {"canonical_url": "https://bsky.app/profile/did:plc:human/post/1"}
    import_posts(conn, [item])
    flush(conn, Batch(deletes=[(uri,)]))
    assert get_post(conn, "bluesky:" + uri) is None


def threads_source(native_id: str = "17900000000000001") -> dict:
    return {
        "platform": "threads",
        "native_id": native_id,
        "author_id": "alice",
        "author_handle": "alice",
        "text": "Original threads text",
        "canonical_url": "https://www.threads.com/@alice/post/abc",
        "observed_at": int(time.time()),
    }


def test_threads_import_round_trips_and_validates_host(conn) -> None:
    assert import_posts(conn, [threads_source()]) == 1
    post = get_post(conn, "threads:17900000000000001")
    assert (post.platform, post.author_key, post.text) == ("threads", "threads:alice", "Original threads text")
    for bad in ("https://x.com/i/status/1", "https://evilthreads.com/@a/post/1", "http://www.threads.com/@a/post/1"):
        with pytest.raises(ValueError):
            import_posts(conn, [threads_source("2") | {"canonical_url": bad}])


def test_threads_media_accepts_meta_cdn_suffixes_only(conn) -> None:
    good = "https://scontent-iad3-1.cdninstagram.com/v/t51/a.jpg?sig=1"
    fbcdn = "https://scontent.xx.fbcdn.net/v/b.jpg"
    bad = ["https://evilcdninstagram.com/a.jpg", "https://cdninstagram.com.evil.io/a.jpg", "http://x.fbcdn.net/a.jpg"]
    item = threads_source() | {
        "author_avatar": good,
        "media": [{"type": "image", "thumb": u} for u in [good, fbcdn, *bad]],
    }
    import_posts(conn, [item])
    post = get_post(conn, "threads:17900000000000001")
    assert [m["thumb"] for m in post.media] == [good, fbcdn]
    assert post.author_avatar == good
