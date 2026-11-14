from curator_core.config import CoreSettings, cached_settings


class CurationSettings(CoreSettings):
    agora_notes_roots: str = ""
    google_application_credentials: str = ""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.1-flash-lite"
    scoring_backend: str = "decisions"
    gemini_decision_model: str = "gemini-decision-1"
    gemma_url: str = ""  # OpenAI-compatible /v1 base URL on Modal
    gemma_token: str = ""
    gemma_model: str = "google/gemma-3-1b-it"
    inference_timeout_s: float = 120
    score_batch_size: int = 20
    score_concurrency: int = 3
    reader_concurrency: int = 4
    label_checks: bool = True  # Bluesky moderation labels via the public AppView
    max_author_posts_per_day: int = 150
    campaign_min_authors: int = 5
    max_tags: int = 6
    curate_interval_s: int = 60
    candidates_per_cycle: int = 200
    candidate_pool_size: int = 2000
    feed_size: int = 50  # Legacy feed ranking compatibility
    minimum_relevance: float = 0.65
    daily_scoring_cap: int = 1000
    score_images: bool = True  # Attach post images/thumbnails to Gemini scoring requests
    max_image_bytes: int = 2_000_000
    image_fetch_timeout_s: float = 10


settings = cached_settings(CurationSettings)
