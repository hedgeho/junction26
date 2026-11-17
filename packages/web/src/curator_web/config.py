from curator_core.config import CoreSettings, cached_settings


class WebSettings(CoreSettings):
    # Signs the website's session cookie.
    session_secret: str = "dev-only-change-me"
    # Shown on the data deletion page when set.
    contact_email: str = ""
    # Reader whose curated feed is shown read-only at /demo; empty disables the demo.
    demo_did: str = ""
    # Date (YYYY-MM-DD) the demo was frozen. When set, only the public pages, the archived demo and
    # the feed generator are served; sign-in, settings, feedback and background sync are off.
    archived_on: str = ""
    for_you_sync_interval_s: int = 60  # Background For You pulls for connected readers; 0 = off


settings = cached_settings(WebSettings)
