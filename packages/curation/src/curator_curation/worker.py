"""Single-host coordinator. Provider calls happen outside database transactions."""

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import UTC, datetime

from curator_core import db

from . import digests, jobs, media, note_folders, profiles
from .candidates import public_candidates
from .config import settings
from .llm.gemini import (
    DECISION_SCORER_VERSION,
    SCORER_VERSION,
    GeminiDecisionScorer,
    GeminiScorer,
    PublicCandidate,
    PublicScoringRequest,
)
from .llm.gemma import GemmaClient

log = logging.getLogger(__name__)


def scorer_version() -> str:
    cfg = settings()
    if cfg.scoring_backend == "decisions":
        return f"{DECISION_SCORER_VERSION}:{cfg.gemini_decision_model}"
    if cfg.scoring_backend == "generative":
        return f"{SCORER_VERSION}:{cfg.gemini_model}"
    raise ValueError("unsupported scoring backend")


def make_scorer(cfg):
    if cfg.scoring_backend == "decisions":
        return GeminiDecisionScorer(cfg.gemini_api_key, cfg.gemini_decision_model, cfg.inference_timeout_s)
    if cfg.scoring_backend == "generative":
        return GeminiScorer(cfg.gemini_api_key, cfg.gemini_model, cfg.inference_timeout_s)
    raise ValueError("unsupported scoring backend")


def score_user(conn, scorer, did: str) -> int:
    cfg = settings()
    uid = profiles.bootstrap(conn, did)
    profile = profiles.head(conn, uid)
    version = scorer_version()
    config = digests.config_for(conn, uid)
    profile_revision = digests.revision(profile)
    terms = json.loads(profile["search_terms"]) if profile else []
    pool = [
        p
        for p in public_candidates(conn, did, terms, cfg.candidate_pool_size)
        if p.author_key not in digests.blocked(config)
    ]
    followed = {r[0] for r in conn.execute("SELECT subject_did FROM follows WHERE user_did=?", (did,))}
    # Public explicit retrieval terms and follows only. Private notes never drive external discovery.
    pool.sort(key=lambda p: (p.author_id not in followed, -sum(t.casefold() in p.text.casefold() for t in terms)))
    unscored = [
        p
        for p in pool
        if not conn.execute(
            """SELECT 1 FROM assessments_v2 WHERE user_id=? AND post_key=?
        AND profile_revision=? AND content_hash=? AND scorer_version=?""",
            (uid, p.key, profile_revision, p.content_hash, version),
        ).fetchone()
    ][: cfg.candidates_per_cycle]
    total = 0
    batches = []
    day = datetime.now(UTC).date().isoformat()
    budget_key = f"scoring-budget:{uid}:{day}"
    prompt = (
        profiles.markdown(conn, uid)
        if profile
        else "Select substantive original posts without assuming the reader's interests."
    )
    for start in range(0, len(unscored), cfg.score_batch_size):
        batch = unscored[start : start + cfg.score_batch_size]
        with db.tx(conn):
            used = int(db.kv_get(conn, budget_key) or "0")
            if used + len(batch) > cfg.daily_scoring_cap:
                break
            db.kv_set(conn, budget_key, str(used + len(batch)))
        described, wanted = media.describe(batch)
        request = PublicScoringRequest(
            prompt,
            tuple(
                PublicCandidate(
                    p.key, p.platform, p.text, p.context, bool(p.parent_id), bool(p.quote_id), described[p.key]
                )
                for p in batch
            ),
        )
        batches.append((batch, request, wanted if cfg.score_images else {}))
    errors = 0
    with ThreadPoolExecutor(max_workers=max(1, min(cfg.score_concurrency, 8))) as executor:
        futures = {
            executor.submit(lambda r, w: scorer.score(replace(r, images=media.fetch_images(w))), request, wanted): batch
            for batch, request, wanted in batches
        }
        for future in as_completed(futures):
            db.kv_set(conn, "curation-heartbeat", str(int(time.time())))
            batch = futures[future]
            try:
                result = future.result()
                expected = {p.key for p in batch}
                ids = [r.post_key for r in result.items]
                if len(ids) != len(expected) or set(ids) != expected:
                    raise ValueError("invalid assessment coverage")
            except Exception:  # noqa: BLE001 - isolate provider failures
                errors += 1
                log.warning("Scoring batch failed; retry next cycle")
                continue
            with db.tx(conn):
                if digests.revision(profiles.head(conn, uid)) != profile_revision:
                    return total
                posts = {p.key: p for p in batch}
                for r in result.items:
                    post = posts[r.post_key]
                    conn.execute(
                        "INSERT OR REPLACE INTO assessments_v2 VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (
                            uid,
                            post.key,
                            profile_revision,
                            post.content_hash,
                            version,
                            r.decision,
                            r.relevance,
                            r.substance,
                            r.opportunity,
                            int(time.time()),
                        ),
                    )
                    total += 1
    if errors:
        raise RuntimeError("One or more scoring batches failed")
    return total


def run_cycle(conn, scorer=None, gemma=None, only_did=None) -> None:
    cfg = settings()
    db.kv_set(conn, "curation-heartbeat", str(int(time.time())))
    for row in conn.execute("SELECT user_id FROM notes_folders").fetchall():
        if only_did:
            account = conn.execute("SELECT id FROM reader_accounts WHERE bluesky_did=?", (only_did,)).fetchone()
            if not account or account["id"] != row["user_id"]:
                continue
        note_folders.sync(conn, row["user_id"])
    if gemma is None and cfg.gemma_url and cfg.gemma_token:
        gemma = GemmaClient(cfg.gemma_url, cfg.gemma_token, cfg.gemma_model, cfg.inference_timeout_s)
    if gemma:
        for _ in range(10):
            if not jobs.process_one(conn, gemma):
                break
    if scorer is None and cfg.gemini_api_key:
        scorer = make_scorer(cfg)
    users = conn.execute(
        "SELECT did FROM users" + (" WHERE did=?" if only_did else ""), (only_did,) if only_did else ()
    ).fetchall()
    for user in users:
        try:
            uid = profiles.bootstrap(conn, user["did"])
            config = digests.config_for(conn, uid)
            last = digests.latest(conn, uid)
            if last and last["day"] == digests.day_key(config) and not config["refresh_requested"]:
                continue
            if scorer is None:
                continue
            db.kv_set(conn, "curation-state:" + uid, "processing")
            current = profiles.head(conn, uid)
            terms = json.loads(current["search_terms"]) if current else []
            if not public_candidates(conn, user["did"], terms, 1):
                continue
            score_user(conn, scorer, user["did"])
            if digests.revision(profiles.head(conn, uid)) != digests.revision(current):
                continue
            digests.build(conn, uid, scorer_version(), gemma)
            db.kv_set(conn, "curation-state:" + uid, "ready")
        except Exception:  # noqa: BLE001 - sanitize external provider errors at the boundary
            account = conn.execute("SELECT id FROM reader_accounts WHERE bluesky_did=?", (user["did"],)).fetchone()
            if account:
                db.kv_set(conn, "curation-state:" + account["id"], "failed")
            log.warning("curation cycle failed for a reader; retry next cycle")


def run_forever() -> None:
    conn = db.connect()
    while True:
        run_cycle(conn)
        time.sleep(settings().curate_interval_s)
