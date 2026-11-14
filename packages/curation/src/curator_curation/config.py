from curator_core.config import CoreSettings, cached_settings


class CurationSettings(CoreSettings):
    anthropic_api_key: str = ""
    # Model for bulk candidate scoring and for profile/feedback interpretation.
    # Scoring volume dominates cost; see DESIGN.md "Cost" before changing.
    scorer_model: str = "claude-opus-5-5"
    profile_model: str = "claude-opus-5-5"
    scorer_effort: str = "low"
    score_batch_size: int = 40

    curate_interval_s: int = 600
    candidates_per_cycle: int = 400
    feed_size: int = 300


settings = cached_settings(CurationSettings)
