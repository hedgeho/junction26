from curator_core.config import CoreSettings, cached_settings


class WebSettings(CoreSettings):
    # Signs the website's session cookie.
    session_secret: str = "dev-only-change-me"
    for_you_sync_interval_s: int = 60  # Background For You pulls for connected readers; 0 = off


settings = cached_settings(WebSettings)
