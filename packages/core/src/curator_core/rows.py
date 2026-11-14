"""Row shapes of the ingest-owned tables, as curation and web read them.

This is the ingest -> curation contract. Keep it in sync with migrations that
touch posts/follows; adding a key is fine, removing or renaming one needs both owners.
"""

from typing import TypedDict


class Post(TypedDict):
    uri: str
    cid: str
    author_did: str
    text: str
    langs: str | None
    created_at: str | None
    indexed_at: int
    reply_parent: str | None
    reply_root: str | None
    embed_kind: str | None
    embed_ref: str | None
    media: str  # JSON list, see curator_core.media
    likes: int
    reposts: int
    replies: int


class Follow(TypedDict):
    user_did: str
    subject_did: str
