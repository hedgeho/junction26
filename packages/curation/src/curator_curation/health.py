"""Fixed operational states, with no model or source text in diagnostics."""

import time

from curator_core import db

from . import digests
from .config import settings

LABELS = {
    "not_configured": "Filtering is not configured.",
    "offline": "Filtering is offline.",
    "failed": "Your selection is temporarily unavailable.",
    "processing": "Filtering posts...",
    "ready": "Ready",
}


def filtering_status(conn, uid):
    cfg = settings()
    model = cfg.gemini_decision_model if cfg.scoring_backend == "decisions" else cfg.gemini_model
    configured = (
        bool(cfg.gemma_url and cfg.gemma_token and cfg.gemma_model)
        if cfg.scoring_backend == "gemma"
        else bool(cfg.gemini_api_key and model and cfg.scoring_backend in {"decisions", "generative"})
    )
    if cfg.scoring_backend != "gemma" and db.kv_get(conn, "notes-in-feed-profile:" + uid):
        configured = False
    if not configured:
        code = "not_configured"
    elif db.kv_get(conn, "curation-state:" + uid) == "failed":
        code = "failed"
    elif digests.latest(conn, uid) and not digests.config_for(conn, uid)["refresh_requested"]:
        code = "ready"
    elif int(db.kv_get(conn, "curation-heartbeat") or "0") < time.time() - max(
        120, cfg.inference_timeout_s + 60, cfg.curate_interval_s * 3
    ):
        code = "offline"
    else:
        code = "processing"
    return {"state": code, "label": LABELS[code]}
