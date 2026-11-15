from curator_core.config import CoreSettings, cached_settings
from pydantic import Field, SecretStr


class IngestSettings(CoreSettings):
    jetstream_url: str = "wss://jetstream2.us-east.bsky.network/subscribe"
    # Empty = keep every language.
    keep_langs: list[str] = Field(default_factory=list)
    retention_hours: int = 48

    follows_ttl_s: int = 6 * 3600
    follows_check_interval_s: int = 300
    # Readers not seen for this long stop getting their follows refreshed.
    active_reader_days: int = 7

    # One operator token. Public search terms are sent to Meta; private notes never are.
    threads_access_token: SecretStr | None = None
    threads_api: str = "https://graph.threads.com"
    threads_poll_interval_s: int = 1800
    # 40 terms every 30 minutes is about 1,920 queries a day, under Meta's 2,200 cap.
    threads_terms_per_cycle: int = 40


settings = cached_settings(IngestSettings)
