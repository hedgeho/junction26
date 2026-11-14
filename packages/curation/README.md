# Quiet curation

The single-host coordinator maintains Markdown configuration using Gemma on Modal, filters public source candidates using Gemini, selects finite daily digests, and matches private note chunks using Gemma. No generated prose is returned to the main reader. Model failures retain working state; there is no Anthropic or private-to-Gemini fallback.

```sh
uv run curator-curate run
uv run curator-curate once --user did:plc:example
```

Set provider credentials/model IDs using `deploy/.env.example`. Modal setup is in `deploy/modal/README.md`. The worker runs without credentials but leaves model-dependent jobs/selections pending.

## Modules

- `profiles.py`: immutable Markdown revisions, compare-and-swap updates and legacy backfill. Public/private documents have separate heads. New public configuration derives only from explicit user input, never notes.
- `jobs.py`: durable, idempotent feedback jobs, per-document leases, bounded retries and interrupted-commit recovery.
- `llm/gemini.py`: explicit public request allowlist, structured classification and complete batch coverage validation.
- `llm/gemma.py`: private profile updates and chunk-ID matching through the authenticated endpoint.
- `notes.py`: immutable imported snapshots, exact Unicode source slices, private shortlist and matches.
- `digests.py`: thresholded daily snapshots, duplicate/conversation grouping, author limits, immediate block/dismiss/delete checks and source-only reader DTOs.
- `worker.py`: provider orchestration outside database transactions, revision-aware score cache and per-user daily candidate budget.
- `candidates.py`, `rank.py`: legacy helpers retained for compatibility/tests; no longer the active reader pipeline.

Daily digest dates use each reader's timezone. Refresh explicitly creates a new edition; ordinary cycles retain the daily snapshot. Public profile changes invalidate the visible old edition until reclassification. Private note matching is best-effort and never substitutes generated text.

Notes start as pasted Markdown/plain text snapshots in settings and queue private-only interest updates by source chunk; there is no external connector. The current private shortlist ranks up to 12 chunks using lexical overlap before Gemma; improve this with a private embedding index if retrieval evaluation warrants it. Notes do not drive external retrieval or the Gemini candidate filter.

## Evaluation

Run the deterministic invariants in `tests/test_quiet_curation.py` before live model evaluation. Capture labeled candidate relevance/exclusions and useful post-note associations separately. Measure precision@k, excluded items, redundancy, profile-change response and source candidate coverage independently. Live quality/latency/cost benchmarking requires configured Gemini and Modal accounts.
