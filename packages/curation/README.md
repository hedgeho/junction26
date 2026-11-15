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

Notes are read-only snapshots from connected Drive folders (up to ten links), with host-local imports retained for operators. The production background worker syncs folders and processes note-profile jobs after feed publication. Matching ranks the top 12 notes by lexical overlap, selects up to six chunks each and evaluates notes against the feed using bounded parallel calls. Successful and empty decisions are cached per note/post pair, scoped to source content, selected chunks, private configuration revision and model. A new post sends only uncached pairs to inference; failed pairs have at most three attempts even when the feed changes. Compact numeric wire indices are mapped back to validated source IDs. The page polls owned source excerpts and adds them as each batch completes. Notes never go to Gemini. Note-derived feed interests require Gemma scoring.

## Evaluation

Post feedback snapshots the source text, author and locally available quote/reply context before queuing. Feed-wide feedback snapshots the visible server-owned selection, with a shared text budget and marked excerpts for long posts. Both paths learn standalone curation principles, never raw feedback transcripts or unresolved phrases such as "more of this". Unresolvable feedback leaves the profile unchanged; invalid model proposals retry without changing the working profile. Revision diffs retain file and hunk headers but omit unchanged lines.

Run the deterministic invariants in `tests/test_quiet_curation.py` before live model evaluation. Capture labeled candidate relevance/exclusions and useful post-note associations separately. Measure precision@k, excluded items, redundancy, profile-change response and source candidate coverage independently. Live quality/latency/cost benchmarking requires configured Gemini and Modal accounts.


Shared Drive listings bypass stale Context.dev snapshots, then download up to four original Markdown files concurrently. Listing completeness, ownership, per-file and overall size limits remain enforced; any failure retains the previous snapshot.

`NOTE_PROFILE_JOBS_PER_CYCLE=1` processes one queued note profile update before matching, rather than ten serial updates. Remaining updates stay queued for following cycles, trading backlog throughput for earlier recommendations. `GEMMA_WARMUP=true` prepares fixed structured-output schemas with synthetic empty results in a separate startup task. It does not block feed publication. Inference timings log only schema, queue/request duration and token counts.
