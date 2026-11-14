"""Settings every process needs. Packages extend CoreSettings with their own fields."""

from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class CoreSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: Path = Path("data")

    # Public hostname this service is reachable at. The feed generator's service
    # DID is did:web:<hostname>, so it must match the TLS hostname exactly.
    hostname: str = "feed.localhost"

    # Account that owns the published app.bsky.feed.generator record.
    publisher_did: str = ""
    feed_rkey: str = "curated"

    public_appview: str = "https://public.api.bsky.app"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "curator.db"

    @property
    def service_did(self) -> str:
        return f"did:web:{self.hostname}"

    @property
    def feed_uri(self) -> str:
        return f"at://{self.publisher_did}/app.bsky.feed.generator/{self.feed_rkey}"


_factories: list = []


def cached_settings[T: BaseSettings](cls: type[T]) -> Callable[[], T]:
    """A cached `settings()` accessor for a settings class; `clear_settings` resets all of them."""
    factory = lru_cache(maxsize=1)(cls)
    _factories.append(factory)
    return factory


def clear_settings() -> None:
    for factory in _factories:
        factory.cache_clear()


core_settings = cached_settings(CoreSettings)
