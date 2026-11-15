from curator_core.config import CoreSettings, cached_settings


class CurationSettings(CoreSettings):
    agora_notes_roots: str = ""
    google_application_credentials: str = ""
    context_dev_api_key: str = ""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.1-flash-lite"
    scoring_backend: str = "decisions"
    gemini_decision_model: str = "gemini-decision-1"
    gemma_url: str = ""  # OpenAI-compatible /v1 base URL on Modal
    gemma_token: str = ""
    gemma_model: str = "google/gemma-3-12b-it"
    inference_timeout_s: float = 120
    note_match_concurrency: int = 4
    gemma_concurrency: int = 2
    note_match_top_notes: int = 12
    note_match_chunks_per_note: int = 6
    score_batch_size: int = 20
    score_concurrency: int = 3
    reader_concurrency: int = 4
    triage_enabled: bool = True  # Shared content flags via Gemini Decisions
    triage_concurrency: int = 8
    label_checks: bool = True  # Bluesky moderation labels via the public AppView
    max_author_posts_per_day: int = 150
    campaign_min_authors: int = 5
    max_tags: int = 6
    curate_interval_s: int = 60
    candidates_per_cycle: int = 400
    candidate_pool_size: int = 4000
    recent_backfill_floor: int = 200  # Unranked recent posts fill the pool only up to this size
    feed_size: int = 50  # Legacy feed ranking compatibility
    minimum_relevance: float = 0.65  # Generative backend scale
    decision_minimum_relevance: float = 0.4  # Decisions backend: grade/3, combined with want >= 0.5
    decision_concurrency: int = 8  # Per-post Decisions requests in flight per scoring batch
    decision_tokens_per_minute: int = 500000  # Input tokens per process, scoring and triage combined; 0 = unlimited
    social_slots: int = 3  # Digest positions reserved for followed authors that pass the filter
    threads_slots: int = 2  # Top digest positions reserved for scored Threads posts, whatever their decision
    serendipity_share: float = 0.1  # Digest share for substantive, clean posts just outside the profile
    hide_shown_posts: bool = True  # Leave posts delivered on earlier days out of later digests
    background_score_interval_s: int = 60  # Score new candidates ahead of refreshes this often
    background_score_size: int = 200  # Candidates scored per background interval
    daily_scoring_cap: int = 30000
    score_images: bool = True  # Attach post images/thumbnails to Gemini scoring requests
    max_image_bytes: int = 2_000_000
    image_fetch_timeout_s: float = 10


settings = cached_settings(CurationSettings)
