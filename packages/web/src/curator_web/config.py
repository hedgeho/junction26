from curator_core.config import CoreSettings, cached_settings


class WebSettings(CoreSettings):
    # Signs the website's session cookie.
    session_secret: str = "dev-only-change-me"
    # Shown on the data deletion page when set.
    contact_email: str = ""
    for_you_sync_interval_s: int = 60  # Background For You pulls for connected readers; 0 = off


settings = cached_settings(WebSettings)
