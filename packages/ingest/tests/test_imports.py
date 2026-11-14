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
