from curator_core.config import CoreSettings, cached_settings


class CurationSettings(CoreSettings):
    gemini_api_key: str = ""
    gemini_model: str = ""  # Set a model available to the event account
    gemma_url: str = ""  # OpenAI-compatible /v1 base URL on Modal
    gemma_token: str = ""
    gemma_model: str = "google/gemma-3-12b-it"
    inference_timeout_s: float = 120
    score_batch_size: int = 20
    curate_interval_s: int = 60
    candidates_per_cycle: int = 200
    candidate_pool_size: int = 2000
    feed_size: int = 50  # Legacy feed ranking compatibility
    minimum_relevance: float = 0.65
    daily_scoring_cap: int = 1000


settings = cached_settings(CurationSettings)
