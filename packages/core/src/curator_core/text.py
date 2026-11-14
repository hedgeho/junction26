"""Text normalization shared by ingest and curation."""

import hashlib
import re

_REFERENCES = re.compile(r"https?://\S+|(?<!\w)[@#][\w.-]+")
_PUNCTUATION = re.compile(r"[^\w]+")
MIN_SIGNATURE_CHARS = 20


def signature(text: str) -> str | None:
    """Equal for copies that differ only in case, punctuation, links, mentions or hashtags."""
    normalized = " ".join(_PUNCTUATION.sub(" ", _REFERENCES.sub(" ", text.casefold())).split())
    if len(normalized) < MIN_SIGNATURE_CHARS:
        return None
    return hashlib.blake2b(normalized.encode(), digest_size=12).hexdigest()
