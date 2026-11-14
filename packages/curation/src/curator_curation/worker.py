"""Single-host coordinator. Provider calls happen outside database transactions."""

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import UTC, datetime

from curator_core import db

from . import digests, jobs, media, note_folders, profiles, screen, triage
from .candidates import public_candidates
from .config import settings
from .llm.gemini import (
    DECISION_SCORER_VERSION,
    SCORER_VERSION,
    GeminiDecisionScorer,
    GeminiDecisionTriager,
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


def make_triager(cfg):
    if cfg.triage_enabled and cfg.scoring_backend == "decisions" and cfg.gemini_api_key:
        return GeminiDecisionTriager(cfg.gemini_api_key, cfg.gemini_decision_model, cfg.inference_timeout_s)
    return None


def score_user(conn, scorer, did: str, timing: dict | None = None, triager=None) -> int:
    cfg = settings()
    timing = {} if timing is None else timing
    started = time.monotonic()
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
    ]
    unscored, timing["screened"] = screen.screen(conn, unscored, followed, cfg.candidates_per_cycle)
    timing["candidates_s"] = round(time.monotonic() - started, 3)
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
    image_s, model_s = [], []

    def run_batch(request, wanted):
        t0 = time.monotonic()
        images = media.fetch_images(wanted)
        t1 = time.monotonic()
        try:
            return scorer.score(replace(request, images=images))
        finally:
            image_s.append(t1 - t0)
            model_s.append(time.monotonic() - t1)

    scoring_started = time.monotonic()
    untriaged = []
    if triager:
        included = {
            r[0]
            for r in conn.execute(
                """SELECT post_key FROM assessments_v2 WHERE user_id=? AND profile_revision=?
                AND scorer_version=? AND decision='include'""",
                (uid, profile_revision, version),
            )
        }
        fresh = {p.key for p in unscored}
        eligible = unscored + [p for p in pool if p.key in included and p.key not in fresh]
        untriaged = triage.missing(conn, eligible, triager.model)
    background = ThreadPoolExecutor(max_workers=1)
    flagged = (
        background.submit(
            triage.assess,
            triager,
            [triage.candidate(p, media.describe([p])[0][p.key]) for p in untriaged],
            cfg.triage_concurrency,
        )
        if untriaged
        else None
    )
    with ThreadPoolExecutor(max_workers=max(1, min(cfg.score_concurrency, 8))) as executor:
        futures = {executor.submit(run_batch, request, wanted): batch for batch, request, wanted in batches}
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
    if flagged:
        results = flagged.result()
        triage.store(conn, untriaged, results, triager.model)
        timing.update(triaged=len(results), triage_failed=len(untriaged) - len(results))
    background.shutdown()
    timing.update(
        scoring_s=round(time.monotonic() - scoring_started, 3),
        batches=len(batches),
        batch_errors=errors,
        scored=total,
        image_fetch_max_s=round(max(image_s, default=0), 3),
        model_call_p50_s=round(sorted(model_s)[len(model_s) // 2], 3) if model_s else 0,
        model_call_max_s=round(max(model_s, default=0), 3),
    )
    if errors:
        raise RuntimeError("One or more scoring batches failed")
    return total


def curate_reader(conn, scorer, gemma, did: str, triager=None) -> None:
    started = time.monotonic()
    uid = None
    try:
        uid = profiles.bootstrap(conn, did)
        config = digests.config_for(conn, uid)
        last = digests.latest(conn, uid)
        if last and last["day"] == digests.day_key(config) and not config["refresh_requested"]:
            return
        if scorer is None:
            return
        db.kv_set(conn, "curation-state:" + uid, "processing")
        current = profiles.head(conn, uid)
        terms = json.loads(current["search_terms"]) if current else []
        if not public_candidates(conn, did, terms, 1):
            return
        timing = {"started_at": int(time.time())}
        score_user(conn, scorer, did, timing, triager)
        if digests.revision(profiles.head(conn, uid)) != digests.revision(current):
            return
        t = time.monotonic()
        run_id = digests.build(conn, uid, scorer_version())
        timing["digest_s"] = round(time.monotonic() - t, 3)
        db.kv_set(conn, "curation-state:" + uid, "ready")
        timing["time_to_ready_s"] = round(time.monotonic() - started, 3)
        if run_id and gemma:
            t = time.monotonic()
            timing["notes_matched"] = digests.match_notes(conn, uid, run_id, gemma)
            timing["notes_s"] = round(time.monotonic() - t, 3)
        timing["total_s"] = round(time.monotonic() - started, 3)
        db.kv_set(conn, "curation-timing:" + uid, json.dumps(timing))
        log.info("curation timing %s", json.dumps(timing))
    except Exception:  # noqa: BLE001 - sanitize external provider errors at the boundary
        if uid:
            db.kv_set(conn, "curation-state:" + uid, "failed")
        log.warning("curation cycle failed for a reader; retry next cycle")


def _curate_in_own_connection(scorer, gemma, did: str, triager) -> None:
    conn = db.connect()
    try:
        curate_reader(conn, scorer, gemma, did, triager)
    finally:
        conn.close()


def run_cycle(conn, scorer=None, gemma=None, only_did=None, triager=None) -> None:
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
    if triager is None:
        triager = make_triager(cfg)
    dids = [
        row["did"]
        for row in conn.execute(
            "SELECT did FROM users" + (" WHERE did=?" if only_did else ""), (only_did,) if only_did else ()
        ).fetchall()
    ]
    if cfg.reader_concurrency <= 1 or len(dids) <= 1:
        for did in dids:
            curate_reader(conn, scorer, gemma, did, triager)
        return
    with ThreadPoolExecutor(max_workers=cfg.reader_concurrency) as executor:
        for future in [executor.submit(_curate_in_own_connection, scorer, gemma, did, triager) for did in dids]:
            future.result()


def pending_work(conn) -> set[tuple]:
    """Everything that should wake the worker before its interval: refresh requests, feedback, new readers."""
    refreshes = conn.execute("SELECT 'refresh', user_id, version FROM reader_settings WHERE refresh_requested=1")
    feedback = conn.execute("SELECT 'feedback', id, 0 FROM feedback_jobs WHERE state='queued'")
    readers = conn.execute("SELECT 'readers', count(*), 0 FROM users")
    return {tuple(r) for r in [*refreshes, *feedback, *readers]}


def wait_for_work(conn, seen: set[tuple], timeout_s: float, poll_s: float = 1.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline and pending_work(conn) <= seen:
        time.sleep(poll_s)


def run_forever() -> None:
    conn = db.connect()
    while True:
        seen = pending_work(conn)
        run_cycle(conn)
        wait_for_work(conn, seen, settings().curate_interval_s)
