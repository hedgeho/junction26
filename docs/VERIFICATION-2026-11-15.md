# Requirements and verification audit - 2026-11-15

This audit covers the implemented Agora reader, curation, notes and feedback requirements discussed across the related development threads. The tested base is `853177c5`, including the generalized profile compiler; `295083db` adds the Junction table location without changing application code. The audit release adds the fixes and reproducible checks below.

## Outcome and methods

- **368 Python tests passed** across all four packages, in 42.03 seconds. Measured line coverage: **86.4%**, 3,692 of 4,273 statements. Coverage identifies exercised code, not correctness of every behavior.
- **27 browser scenario groups passed**, using 100 real signed-in Bluesky source posts in an isolated database; zero uncaught browser exceptions.
- **Seven focused feedback-recovery checks passed**, including actual HTTP submission, stored jobs, receipt recovery after a forced primary 404, reloads, exact diff rendering and duplicate-free retry. The model in these browser checks is deterministic.
- **17 axe-core 4.14.0 WCAG A/AA state scans passed** with no detected violations. Desktop light/dark feed, feedback, limit dialog, settings, history and setup; landing; mobile feed, feedback, limit dialog and settings. Incomplete automated checks, including some contrast cases, still require human review. This is not an accessibility certification.
- Read-only live checks reached Bluesky, the shared Drive folder, Gemini Decisions and the Modal Gemma endpoint. No original posts or notes were changed. Browser publishing actions were intercepted; they did not create real likes, replies or reposts.
- Visual review checked the existing design, post identities, source media, feedback panel and mobile settings. Browser layout checks cover 320, 390, 768 and 1,440 pixels, keyboard submission, image overlays, drafts and theme persistence.

The later UI decision takes precedence over older requests: the bottom composer has one **Tell your feed** action that saves preferences for future picks. The visible **Show more** button was retired. The legacy intentional-selection backend remains covered for compatibility.

## Requirements matrix

Paths below are relative to the repository. Backend tests are deterministic unless explicitly marked live; browser tests use the isolated server in `scripts/check_reader_ui.py`.

| Requirement | Evidence and result |
| --- | --- |
| Authentic source feed; no production placeholders | Browser renders ten real stored source posts; live saved-session Bluesky fetch returns 100 posts with valid identities. `web/tests/test_bluesky_feed.py`, `curation/tests/test_candidates_rank.py`. The harness alone creates controlled failure responses. |
| Original algorithm and platform-neutral data | Candidate/rank, source import and source-contract tests cover Bluesky and X, author limits and deduplication. Live X collection/login/actions remain unimplemented. |
| No initial interests required | Reader/quiet-curation tests cover the default selection without profile interests or model keys; initial target ten. |
| Strict editable daily target | Browser rejects zero and applies the new limit immediately; digest quota/reader tests enforce final deterministic clipping even when model output is excessive. |
| Stable day-based editions | `test_digest_quotas.py`, `test_quiet_curation.py` and `test_reader.py` cover repeat reads, day rollover, prior-day exclusion and owned selection replacement. |
| Whole-feed assembly after suitability scoring | `test_partial_scoring.py`, `test_worker_latency.py` and quiet-curation tests cover bounded selection, ownership and incomplete/failed scoring. Live Gemma selected one valid owned post under cap one. |
| Names, handles, avatars, links, media and quotes | Real browser source rendering; `test_post_details.py`, `test_appview.py`, source/media tests. Hydration of 51 posts uses 25/25/1 requests and propagates partial provider failure. |
| In-app likes, saves and their filled state | Browser toggles and failure recovery; `web/tests/test_social.py` verifies provider contracts, user ownership and CSRF. No live remote write was performed. |
| Single repost menu; direct/quote outlines | Browser exercises both actions, contextual quote composer, result outline and deletion; backend social tests cover strong references and own record deletion. |
| Comments, nested replies and own deletion | Browser covers incremental branches to six levels, same reply interaction controls, own comment display/deletion and draft retention. `test_post_details.py`, `test_social.py`, `test_appview.py` cover API structure and ancestors. |
| Share and image overlay | Browser checks canonical sharing, in-place image opening, Escape and return to the reader. |
| Feedback separate from social actions | Browser verifies the side panel, selected source context, original-post link, independent per-post drafts and no automatic social publication. |
| Feed feedback has the whole visible set | Reader/quiet-curation tests validate owned visible feed context, source quote/parent context and bounded shared context size. Browser verifies the submitted context. |
| Interpret nuanced instructions | `test_profile_compilation.py`, `test_profile_edits.py` and Gemma tests cover keep/patch/replace, small edits, broad rewrites, clearing and deletion-only changes. Related compiler release reported seven passing live cases, including temporary intent and untrusted-source isolation; these were not seven independent repeated runs by this audit. |
| No unsupported generic preference from ?more of this? | Unresolved references and absent source context fail closed; test coverage prevents saving prompt-example interests as a valid update. Actual model quality beyond the tested cases remains probabilistic. |
| Durable historical interests versus transient requests | Compiler tests distinguish persistent profile edits and request criteria. Legacy immediate-selection requests use the current request as primary context with separate cached score versions. No fixed historical/current/momentary time decay is implemented. |
| Exact per-feedback summary, diff and reload receipt | Seven recovery browser checks plus `test_profile_diffs.py` and `test_feedback_recovery.py`; immutable revision association, literal rendering, no-op attribution and reader-scoped lookup. |
| Idempotency despite source disappearance and concurrent retries | New tests accept feedback, remove its source, and replay the original key; original job and source snapshot remain. Eight simultaneous submissions create exactly one job. New submissions for vanished sources remain rejected. |
| Markdown configuration, complete history and restore | Browser and profile tests cover both config files, full diff, pagination, exact original downloads, Unicode/line endings, optimistic revision conflicts and restorable broad edits. |
| Private Gemma boundary | Notes, worker and compiler tests prevent private note/profile material falling back to Gemini. With Gemini scoring, note-derived updates affect the private matching profile; evolving feed curation from notes requires Gemma scoring. |
| Original note excerpts only; no note modification | Matching reconstructs exact source slices and checks hashes. Tests reject foreign chunks, stale sources and altered outputs; browser ?Use as comment? retains exact text, requires confirmation and blocks over-limit submission. |
| Multiple hosted notes links | Folder tests cover normalization, duplicate links, multiple sources, bounded concurrent download, atomic failed import and connection cleanup. Browser verifies connection controls, processing disclosure/privacy link and Threads coming soon. |
| Shared Drive folder really contains seven notes | Live fresh Context.dev scan imports seven files, 14,790 characters; source hashes valid. This tests application import code and configured credentials, not the production user's stored connection. |
| Periodic notes analysis and cache invalidation | `test_note_folders.py`, `test_note_match_cache.py`, `test_note_relevance.py` and quiet-curation tests cover source/model/profile hash invalidation, positive/empty caches, deletion during inference, retry bounds and note-derived updates. |
| Feed first, notes arrive later | Worker-latency tests prove digest publication precedes notes/model warm-up; browser streams note matches after the feed and preserves drafts. Matching is bounded parallel work per shortlisted note. |
| No meaningless note recommendation | Live relevant passage matched at 0.95; unrelated and instruction-injection posts produced no match. Threshold unchanged. Exact useful suggestions for every real feed are not guaranteed. |
| Graceful model/network errors | Rate-limit, partial-scoring, worker and browser tests cover retry/backoff, stale inference, missing keys, unavailable endpoints, retained drafts and honest partial/empty/unavailable note states. |
| Read-time deletions/blocks/source edits | Source, import, candidate and quiet-curation tests enforce invalidation. New retention test ensures an old normalized tombstone cannot expose a newer legacy copy. |
| Follow refresh failures do not erase the graph | New pagination and atomic refresh tests preserve old follows after HTTP failure, pass cursors correctly, replace completed results and deduplicate accounts. |
| Authentication, CSRF and reader ownership | Reader/social/feedback tests reject cross-user jobs, source chunks, invalid tokens and unsafe mutations; exact package tests enforce dependency ownership. Fresh production OAuth was not repeated. |
| Ctrl+Enter and drafts | Browser exercises both feedback scopes, daily target, social composers and configuration forms; duplicate submission guards and context-specific drafts remain intact. |
| Read-only public demo | Existing reader tests verify configured demo access, no anonymous reader creation, no private notes, no interaction controls and correct feed funnel. Demo endpoint hooks are preserved. |

## Measured performance

Measurements below are small local or live-provider samples on November 15, not production SLOs. Read tests use a temporary copy of real data, a signed-in reader and disabled background providers. They do not include model inference, public CDN downloads or end-to-end internet latency. Per-route p95 uses the nearest-rank method with 15 samples.

| Read path | Median | p95 |
| --- | ---: | ---: |
| Feed `/me` | 25.5 ms | 38.1 ms |
| Settings | 10.1 ms | 16.6 ms |
| Curation status | 8.5 ms | 11.2 ms |
| Note-match polling | 19.2 ms | 37.3 ms |
| Feed explanation funnel | 24.4 ms | 35.3 ms |
| Configuration history | 9.0 ms | 11.7 ms |

Forty feed reads at concurrency eight completed in **2.024 seconds**, with **zero errors**, 381.8 ms median and 492.0 ms p95 measured inside the concurrency slot. This small test does not establish multi-user production capacity.

| Live operation | Observed time | Scope |
| --- | ---: | --- |
| Signed-in Bluesky source read | 1.266 s | 100 posts; identity checks passed |
| Fresh shared Drive import | 5.156 s | Seven files; 14,790 characters; hashes valid |
| Gemini Decisions public scoring | 1.078 s | Two real public posts; complete valid result set |
| Gemma final feed selection | 2.313 s | Two candidates, one valid selected post under cap one |
| Gemma relevant note matching | 3.938 s | Controlled relevant/unrelated pair; exact source passage |
| Gemma unrelated-only matching | 0.578 s | Empty result |
| Gemma source-instruction isolation | 0.593 s | Empty result for malicious post instruction |

The configured models were `gemini-3.1-flash-lite`, Gemini Decisions and `google/gemma-3-12b-it`. Earlier warm structured calls took 14-54 seconds for feedback and sometimes considerably longer for note matching. Warm-up reduces cold-start risk; queueing, decoding and provider failures can still delay requests. This audit does not establish a uniform speedup.

## Warmth and operational limits

`deploy/modal/gemma_service.py` declares one minimum/maximum L40S container and two concurrent inputs. A local keep-warm process is scheduled until **13:08 Helsinki on November 15**. At 11:34 Helsinki it reported a successful approximately 0.5-second ping, 126 successes and ten historical connection failures. This is health evidence, not proof of uninterrupted availability. Modal management credentials were not available, so the deployed autoscaling settings could not be independently inspected or redeployed in this audit. The local keeper does not promise permanence beyond its deadline.

Additional limits:

- A complete new production OAuth/reconnect flow and actual social writes were not repeated. Browser writes were deliberately simulated; backend contracts were tested. Signing in with an old session can require reconnecting for deletion permission.
- Live X collection/login/interactions are absent; normalized import, classification and rendering are implemented. LinkedIn/Substack and other file providers are extension points. Threads remains marked coming soon in the user settings despite optional operator ingestion support.
- Link-shared Drive folders work through Context.dev. A per-user private Drive OAuth flow is absent. Context.dev scans preserve previously imported files when a new listing omits them, because listings have been unreliable; disconnect removes imported notes/matches/jobs. Listing omission alone is not a verified deletion signal.
- Connected notes are read-only application snapshots. Original files are never written, but inferred private preferences can remain in configuration history after a note is removed.
- Source retention is 48 hours. There is no validated semantic topic quota, automatic full account-erasure policy or production-scale load test in this audit.
- The literal configuration diff is reliable evidence of the saved change. Human interpretation, nuanced relevance, adversarial-model resistance and feed coherence require ongoing evaluated examples; finite tests cannot guarantee every future model response.

## Repeatable verification

```sh
uv sync --all-packages --frozen
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
node --check packages/web/src/curator_web/static/app.js
uv run --with playwright python scripts/check_reader_ui.py
uv run --with playwright python scripts/check_feedback_recovery.py
uv run --with playwright python scripts/check_reader_performance.py
uv run --with playwright python scripts/check_accessibility.py --axe-script /path/to/axe-core-4.14.0/axe.min.js
uv run --with pytest-cov pytest --cov=curator_core --cov=curator_ingest --cov=curator_curation --cov=curator_web
uv run curator-curate check-providers
```

Browser, accessibility and read-performance checks require at least ten real local posts and Chromium, or Microsoft Edge on Windows. They copy local SQLite/profile state into temporary storage, prevent remote social writes and remove the temporary server afterward. The accessibility check requires an existing axe-core browser bundle and exits unsuccessfully on detected violations; no third-party bundle is added to production. Provider checks need configured credentials and make real billable calls.

Ignored local artifacts include `data/test-results-final.xml`, `data/coverage-final.json`, `data/ui-audit/browser-report.json`, `data/accessibility.json`, `data/reader-performance.json`, the live probe reports and screenshots. No credential files, personal note contents or account-specific live-probe scripts are committed.
