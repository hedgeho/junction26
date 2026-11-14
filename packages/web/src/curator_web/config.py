from curator_core.config import CoreSettings, cached_settings


class WebSettings(CoreSettings):
    # Signs the website's session cookie.
    session_secret: str = "dev-only-change-me"


settings = cached_settings(WebSettings)
