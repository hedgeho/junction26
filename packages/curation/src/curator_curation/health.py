"""Fixed operational states, with no model or source text in diagnostics."""

import time

from curator_core import db

from . import digests
from .config import settings

LABELS = {
    "not_configured": "Filtering is not configured.",
    "offline": "Filtering is offline.",
    "failed": "Filtering temporarily unavailable. Retrying.",
    "processing": "Filtering posts...",
    "ready": "Ready",
}


def filtering_status(conn, uid):
    cfg = settings()
    if not cfg.gemini_api_key or not cfg.gemini_model:
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
