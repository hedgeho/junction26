from curator_core.config import CoreSettings, cached_settings
from pydantic import Field


class IngestSettings(CoreSettings):
    jetstream_url: str = "wss://jetstream2.us-east.bsky.network/subscribe"
    # Empty = keep every language.
    keep_langs: list[str] = Field(default_factory=list)
    retention_hours: int = 48

    follows_ttl_s: int = 6 * 3600
    follows_check_interval_s: int = 300
    # Readers not seen for this long stop getting their follows refreshed.
    active_reader_days: int = 7


settings = cached_settings(IngestSettings)
