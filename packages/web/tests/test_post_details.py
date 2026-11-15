from curator_web import post_details


def test_bluesky_details_preserve_utf8_facets_and_original_media():
    text = "Hello 🦋 link"
    start = len("Hello 🦋 ".encode())
    view = {
        "author": {
            "did": "did:plc:writer",
            "handle": "writer.bsky.social",
            "displayName": "Writer",
            "avatar": "https://cdn.bsky.app/avatar.png",
        },
        "record": {
            "text": text,
            "facets": [
                {
                    "index": {"byteStart": start, "byteEnd": start + 4},
                    "features": [{"$type": "app.bsky.richtext.facet#link", "uri": "https://example.org/"}],
                }
            ],
        },
        "embed": {
            "$type": "app.bsky.embed.images#view",
            "images": [
                {
                    "thumb": "https://cdn.bsky.app/thumb.png",
                    "fullsize": "https://cdn.bsky.app/full.png",
                    "alt": "A bird",
                }
            ],
        },
    }
    result = post_details.bluesky_details(view, text)
    assert result["author_name"] == "Writer"
    assert result["author_handle"] == "writer.bsky.social"
    assert result["facets"] == [{"start": start, "end": start + 4, "url": "https://example.org/"}]
    assert result["images"][0]["alt"] == "A bird"
    stale = post_details.bluesky_details(view, "Different original text")
    assert stale["facets"] == [] and stale["images"] == []


def test_unsafe_links_and_images_are_not_returned():
    text = "test"
    view = {
        "author": {"avatar": "javascript:alert(1)"},
        "record": {
            "text": text,
            "facets": [
                {
                    "index": {"byteStart": 0, "byteEnd": 4},
                    "features": [{"$type": "app.bsky.richtext.facet#link", "uri": "javascript:alert(1)"}],
                }
            ],
        },
        "embed": {
            "$type": "app.bsky.embed.images#view",
            "images": [{"thumb": "http://insecure.example/img", "fullsize": "https://example.org/img"}],
        },
    }
    result = post_details.bluesky_details(view, text)
    assert not result["author_avatar"]
    assert result["facets"] == [] and result["images"] == []


def test_malformed_optional_metadata_does_not_hide_original_text():
    text = "Original link"
    record = {
        "text": text,
        "facets": [
            {"index": "invalid", "features": []},
            {"index": {"byteStart": 0, "byteEnd": 8}, "features": [None, "invalid"]},
        ],
    }
    assert post_details.facets(record, text) == []
    for embed in (
        {"$type": "app.bsky.embed.recordWithMedia#view", "media": "invalid"},
        {"$type": "app.bsky.embed.external#view", "external": ["invalid"]},
    ):
        assert post_details.media(embed) == ([], None)
