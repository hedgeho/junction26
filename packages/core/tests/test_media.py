import hashlib
import json

from curator_core import media
from curator_core.imports import import_posts
from curator_core.sources import SocialPost, get_post

DID = "did:plc:writer"
CID = "bafkreigh2akiscaildcqabsyg3dfr6chu3fgpregiymsck7e7aqa4s52zy"


def blob(cid=CID):
    return {"$type": "blob", "ref": {"$link": cid}, "mimeType": "image/jpeg", "size": 1000}


def test_bluesky_images_video_links_and_quote_media():
    images = {
        "$type": "app.bsky.embed.images",
        "images": [{"alt": "A chart", "image": blob(), "aspectRatio": {"width": 4, "height": 3}}],
    }
    assert media.bluesky_media(DID, {"embed": images}) == [
        {
            "type": "image",
            "thumb": f"https://cdn.bsky.app/img/feed_thumbnail/plain/{DID}/{CID}@jpeg",
            "alt": "A chart",
            "full": f"https://cdn.bsky.app/img/feed_fullsize/plain/{DID}/{CID}@jpeg",
            "aspect": [4, 3],
        }
    ]
    video = {"$type": "app.bsky.embed.video", "video": blob(), "alt": "Talk"}
    assert media.bluesky_media(DID, {"embed": video})[0]["thumb"] == (
        f"https://video.cdn.bsky.app/hls/{DID}/{CID}/thumbnail.jpg"
    )
    external = {
        "$type": "app.bsky.embed.external",
        "external": {"uri": "https://example.com/a", "title": "Paper", "description": "Abstract", "thumb": blob()},
    }
    [link] = media.bluesky_media(DID, {"embed": external})
    assert (link["type"], link["url"], link["title"]) == ("link", "https://example.com/a", "Paper")
    quote = {"$type": "app.bsky.embed.recordWithMedia", "record": {"record": {"uri": "at://x"}}, "media": images}
    assert media.bluesky_media(DID, {"embed": quote})[0]["alt"] == "A chart"


def test_untrusted_media_references_are_dropped():
    bad_cid = {"$type": "app.bsky.embed.images", "images": [{"image": blob("../../evil")}]}
    assert media.bluesky_media(DID, {"embed": bad_cid}) == []
    assert media.bluesky_media("not-a-did", {"embed": {"$type": "app.bsky.embed.images", "images": []}}) == []
    items = [
        {"type": "image", "thumb": "https://evil.example/x.jpg"},
        {"type": "image", "thumb": "http://pbs.twimg.com/x.jpg"},
        {"type": "link", "url": "javascript:alert(1)"},
        {"type": "image", "thumb": "https://pbs.twimg.com/a.jpg", "full": "https://evil.example/a.jpg"},
    ] + [{"type": "image", "thumb": "https://pbs.twimg.com/b.jpg"}] * 5
    cleaned = media.clean("x", items)
    assert len(cleaned) == media.MAX_IMAGES
    assert cleaned[0]["full"] == "https://pbs.twimg.com/a.jpg"
    assert media.clean("bluesky", [{"type": "image", "thumb": "https://pbs.twimg.com/a.jpg"}]) == []


def test_import_stores_media_and_author_display_metadata(conn):
    avatar = f"https://cdn.bsky.app/img/avatar/plain/{DID}/{CID}@jpeg"
    record = {
        "platform": "bluesky",
        "native_id": f"at://{DID}/app.bsky.feed.post/1",
        "author_id": DID,
        "author_handle": "writer.bsky.social",
        "author_avatar": avatar,
        "text": "With a picture",
        "canonical_url": f"https://bsky.app/profile/{DID}/post/1",
        "media": [{"type": "image", "thumb": media.bluesky_image(DID, CID), "alt": "Chart"}],
    }
    import_posts(conn, [record])
    post = get_post(conn, "bluesky:" + record["native_id"])
    assert post.media[0]["alt"] == "Chart"
    assert (post.author_handle, post.author_avatar) == ("writer.bsky.social", avatar)
    import_posts(conn, [record | {"author_avatar": "https://evil.example/a.jpg"}])
    assert get_post(conn, "bluesky:" + record["native_id"]).author_avatar is None


def test_media_changes_hash_but_text_only_hash_is_stable():
    base = SocialPost("k", "x", "1", "a", "a", "text", "https://x.com/i/status/1", None, 0)
    with_media = SocialPost("k", "x", "1", "a", "a", "text", "https://x.com/i/status/1", None, 0, media=({"a": 1},))
    # The pre-media hash definition; existing assessments must stay valid.
    legacy = hashlib.sha256(json.dumps(["text", "a", None, None, None, None, ""]).encode()).hexdigest()
    assert base.content_hash == legacy
    assert with_media.content_hash != legacy
