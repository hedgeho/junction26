# Technical plan: quiet social curation and private notes matching

Date: 2026-11-14. Reviewed repository: `956622f` (`deploy: Hostinger hackathon box at feed.ilia.fi`).
Status: implementation reference. Core daily-reader flow implemented; see DESIGN.md for integration limits and live validation requirements.
This plan supersedes conflicting product and model choices in DESIGN.md; that document remains a record of the initial skeleton.

## 1. Product contract

The product selects original social posts and optionally places relevant, verbatim passages from the user's notes beside them. A user gives natural-language feedback; the agent silently updates Markdown configuration. The normal interface shows a fixed status such as "Updated", never a generated response.

Non-negotiable behavior:

- Never write to, rewrite, generate continuations of, or publish posts or source notes. No publishing credentials or write-capable note connector belongs in this implementation.
- Never expose generated explanations, summaries, replies, suggested wording, model analysis, or inferred-interest prose in the reader, notifications, or normal API responses.
- Generated prose is permitted inside configuration files, accessible only through a secondary settings surface the user deliberately opens.
- Recommendations are selections and associations: original post A beside original passage B. Render source text from storage, never text returned by a model.
- The user controls the interests and budget. A digest may contain fewer than k items, including zero.
- Private notes and note-derived interests stay outside Gemini. Self-hosted Gemma on Modal processes them. Modal and the application host remain trusted cloud processors; this is not on-device or end-to-end encrypted inference.

## 2. Architecture and ownership

Keep the existing single-host state architecture for the proof of concept. Hostinger runs ingest, the curation coordinator, and FastAPI with the existing local SQLite WAL database. Modal supplies authenticated Gemma inference. The coordinator calls the Gemini API for public-post classification. A Modal CPU function may host that stateless Gemini operation as a later step; it must receive bounded inputs and return results rather than mount the application database.

Data flow:

1. Pipeline produces candidate posts and source metadata.
2. Web records feedback as a durable job and returns a fixed acknowledgement.
3. Curation calls Gemma on Modal to update the user's Markdown configuration.
4. Curation validates the revision and atomically makes it current; derive retrieval terms separately.
5. Candidate retrieval selects a bounded public-post pool using the explicit filtering configuration.
6. Gemini classifies the pool using only that configuration and allowlisted public fields.
7. Gemma matches selected public posts against private note chunks and private interests.
8. Local deterministic selection produces a finite digest; web renders only source posts and verified source slices.

Most business logic belongs in `packages/curation`; routes and presentation belong in `packages/web`. Additive schema work belongs in `packages/core`. Modal runtime definitions and deployment settings require a small change under `deploy/` with the deployment owner. Ingest remains responsible for source facts, lifecycle changes, and candidate coverage.

Preserve dependency direction: web -> curation -> core; ingest -> core. Do not import ingest code from curation. Modal deployment code invokes a narrow curation inference adapter rather than becoming a dependency of ingest.

## 3. Model roles and data boundaries

| Task | Model/runtime | Input | Output retained internally |
| --- | --- | --- | --- |
| Explicit preference updates | Gemma on Modal | Current Markdown, feedback, referenced source item if needed | Proposed Markdown revision |
| Private note understanding | Gemma on Modal | Read-only note chunks, private configuration | Private topic metadata and chunk selections |
| Public candidate classification | Gemini API | Explicit filtering profile, public candidates, permitted context | Structured decisions and scores |
| Post-to-note matching | Gemma on Modal | Selected public post/context, private chunks | Source chunk IDs and relevance values |
| Digest assembly | Local Python | Validated assessments and user constraints | Selected source IDs and verified note spans |

Use Gemma for configuration by default: it is allowed by the model requirement and avoids a second configuration runtime for private inputs. Gemini remains the substantive final public-post classifier. Do not retain the Anthropic path as a fallback.

Maintain two Markdown documents:

- `profile.md`: explicit social filtering instructions. This is the deliberately shared configuration used by Gemini. Preserve goals, preferences, qualifications, exceptions, temporary priorities, and exclusions.
- `private-interests.md`: understanding inferred from notes, processed only by Gemma. Never automatically copy its contents into `profile.md`.

The feedback interface must make the boundary clear using fixed copy: explicit filtering preferences are used with Gemini; source notes are processed with self-hosted Gemma. A private-feedback mode can update only `private-interests.md`. Notes-like content submitted through a private workflow must never become a public-profile update.

Gemini request construction must use a dedicated allowlisted request type, not a generic user object or serialized profile bundle. Do not include note IDs/titles/text, note embeddings, private interests, source feedback history, or Gemma analysis. In the initial version private matching only reorders/annotates the public-qualified pool; it does not use private keywords to make external discovery requests. This limits discovery coverage but preserves the boundary.

## 4. Markdown configuration lifecycle

Add a `profiles/` module in curation with a storage service, updater, validation, and bounded retrieval-term derivation. Markdown is the authoritative expression of preferences; normalized rules/search terms are versioned derived artifacts.

Suggested document sections: Goals; Topics and desired depth; Prefer; Avoid; Exceptions and nuance; Temporary priorities; Discovery and diversity. Store enforceable delivery settings such as k, timezone, cadence and explicit blocked account IDs as validated structured settings, editable through fixed UI controls. Do not rely on natural-language text to enforce a hard block.

Storage proposal:

- `data/profiles/<opaque-user-id>/<document-kind>/<revision-id>.md`: immutable UTF-8 revisions outside the public/static tree.
- SQLite stores the current revision pointer, content hash, parent revision, source event, model/prompt version and timestamps.
- Write a new revision using a temporary file and same-filesystem atomic rename, then switch the DB pointer in a transaction conditional on the expected parent revision. Failed races leave an unused revision, never overwrite another update; clean unused revisions later.
- All reads resolve the DB pointer; never overwrite a shared `current.md` symlink/file as the concurrency mechanism. The download route serves the current revision as `profile.md`.
- Back up configuration files and their revision pointers together. Keep these files and note stores out of Git, images, static serving and ordinary logs.

Update algorithm:

1. Persist feedback with user ownership, an idempotency key, the target document and expected revision.
2. Serialize jobs per user/document; obtain the current document at processing time.
3. Ask Gemma for a replacement Markdown document only. Preserve unrelated preferences, negation, exceptions and the distinction between a temporary request and a standing instruction. Quoted posts/notes are data, not instructions.
4. Validate size, encoding and allowed shape; reject unexpected tool/action requests. Model output has no file paths or write authority.
5. Commit via compare-and-swap; recompute against the newest revision after a conflict. Do not automatically overwrite with a stale proposal.
6. Derive retrieval terms/rule metadata; publish that derived configuration only when its revision matches the Markdown revision.
7. Mark feedback applied and invalidate affected selection work. A failed update leaves the last working revision and shows a fixed retry/error state.

Allow an optional settings page to inspect, download, edit or restore Markdown revisions. A user edit uses the same version check. Do not display a generated diff or explanation in the primary workflow. Statuses are fixed `queued`, `processing`, `applied`, `failed` values.

## 5. Gemini classification and deterministic selection

Replace `llm/scorer.py` with a provider-specific Gemini adapter behind a small typed scoring interface. Use the Google GenAI SDK, structured JSON output, and local Pydantic validation. Structured syntax alone does not establish decision correctness [S1].

Proposed internal assessment:

- `post_key`, `profile_revision`, `content_hash`, `scorer_version`.
- `decision`: include / exclude / insufficient_context.
- Bounded `relevance`, `substance`, and `conversation_opportunity` scores; these are rubric scores, not calibrated probabilities.
- `matched_rule_ids`, `exclusion_rule_ids`, and fixed `context_flags`.
- `model_id`, prompt/schema versions, timestamps and usage counters.

No generated `reason` is needed. Return no freeform commentary. Validate exactly one result for each requested candidate, no duplicates, no unknown IDs, valid rule references and finite bounded numbers. Retry bounded missing/invalid subsets; never interpret missing output as a positive assessment. Enforce account blocks and other structured hard exclusions in code before and after model calls.

Version cache keys by user, public profile revision, public content/context hash, model, prompt and schema. The current `(user_did, post_uri)` cache is insufficient. Profile updates make existing candidates eligible for rescoring; results arriving for an obsolete revision cannot enter a new digest. Include parent/quoted context when available and request it through the pipeline contract; a bare reply may legitimately be unjudgeable.

Separate the public relevance decision from final digest assembly:

- Minimum usefulness threshold; never fill k with poor matches.
- Group one conversation and repeated/shared links to avoid near-duplicate items.
- Apply per-author limits and explicit topic/exploration settings.
- Track seen/dismissed content and delivery history. Post selection does not imply permission to publish.
- Prefer source timestamps for freshness, with an explicit missing-timestamp fallback. `indexed_at` is arrival time and can make replayed content appear fresh.
- Do not use engagement as the default objective. Retain it only as an optional bounded signal; approximate counters are not truth labels.

Materialize a digest snapshot with an explicit period, cutoff, profile revision and selection version. Feedback can trigger a new snapshot; do not silently mutate a delivered edition. A week/month option requires storage compatible with that horizon, described below. Start with daily delivery and a refresh action.

## 6. Read-only notes processing and exact-source matching

Start with user-uploaded UTF-8 Markdown/plain text snapshots; connector synchronization is a later independent adapter. A connector must request read-only access. No workflow writes back to a notes provider or original file.

Treat imported note text as immutable per version. Assign opaque note/version IDs and derive chunks deterministically using Unicode character offsets. Store source hash, title/provenance and ownership. Chunking, metadata and embeddings are separate derived records; updating an import produces a new source version.

For the initial small note collection, shortlist with private local full-text retrieval and have Gemma rank chunk IDs. Gemma may also derive private topic metadata. Do not add an embedding service until retrieval quality/size requires it; any later embeddings remain within the private processing boundary.

For matching, send bounded private chunks and public conversation context to Modal. Ask Gemma to select existing chunk IDs, not write excerpts. The application retrieves the exact chunk slice from the immutable source. If finer spans are added later, validate that offsets fit the selected chunk/version and return exactly that substring; discard invented, altered or stale spans.

Show source title/link and original passage only. A fixed label such as "From your notes" is sufficient. No generated bridge sentence. Note deletion or replacement invalidates all affected chunks and matches; remove them from every subsequent response, including old digest views. Avoid persisting private source bodies in Modal volumes; use a weights/cache volume and transient inference inputs. Set explicit application retention for imports, feedback and derived records, and document backup/provider retention separately.

## 7. Web changes

Update `site.py` and `templates/me.html`, keeping FastAPI/Jinja for the first implementation:

- Make the standalone finite reader the main surface. Bluesky feed publication is an optional delivery adapter.
- Replace synchronous feedback inference in the request with durable job submission and status polling. The worker owns application of updates; fixed acknowledgements only.
- Remove rendered generated reasons and the prominent generated preference list. Remove those fields from normal response schemas rather than hiding them with CSS.
- Move configuration documents to authenticated secondary settings/download/edit routes. Escape rendered Markdown or offer plain text; generated Markdown must not execute HTML or scripts.
- Render original source posts with attribution/link and verified note passages. Use platform adapters for hydration; don't route all sources through Bluesky AppView.
- Add fixed feedback actions and optional free-text qualification. Every feedback job has one document target and user identity from the authenticated session.
- Validate ownership on profiles, source notes, feedback jobs, digests and note matches. Add CSRF protection to cookie-authenticated mutation routes, bounded input sizes, opaque storage paths and sanitized fixed error messages.
- Preserve selection order after source hydration. Missing/deleted source items disappear without backfilling from a global popular feed.

The current preview only returns 30 items and exposes `reason`; neither is a sufficient digest contract. Add reader DTOs that cannot contain model-authored prose. Also audit error pages, email, accessibility text, tooltips, logs exposed through UI, and any existing feed interaction response.

## 8. Shared schema and cross-platform contract

Use new numbered migrations; never edit 0001/0002. Coordinate shared changes with the pipeline owner before implementation.

Proposed additive records:

- `profile_revisions` and `profile_heads`: immutable configuration metadata and current pointers.
- `feedback_jobs`: user, target document, source item reference, idempotency key, state, lease/retry metadata and fixed error code.
- `curation_settings`: validated budgets/cadence/blocks with a version; structured settings are distinct from Markdown prose.
- `assessments_v2`: versioned public classifications and machine-only flags.
- `digest_runs` / `digest_items`: snapshot edition, selection version, ordered source references and user ownership.
- `note_sources` / `note_versions` / `note_chunks` / `note_matches`: immutable source snapshots, derived indexes and exact-source references.

Use an internal opaque user ID and platform-qualified `(platform, native_id)` actor/post references for new contracts. Maintain a mapping from the existing Bluesky DID users; preserve `users.did`, `last_seen_at`, posts/follows and feed-generator interfaces until ingest migration is agreed. Add account identity mappings rather than using an X handle as a DID. Creating an internal user model does not implement X sign-in; Bluesky login can still identify the demo user who reads candidates from multiple sources.

Ask the pipeline owner for the minimal normalized contract: platform, native post ID, canonical URL, actor reference, original text, created/observed timestamps, language, parent/root/quoted references, available context, native source version/content hash, deletion/edit state, and candidate-source provenance. Content eligibility/visibility is a source concern. Preserve native metadata alongside normalized fields.

Prefer a curation-side adapter from existing Bluesky rows into this contract for the first milestone. X requires an actual source adapter and access decisions; adding a platform enum alone is not an integration. LinkedIn/Substack are future adapter targets with independent feasibility checks.

## 8a. Proposed implementation files

Keep the existing files as integration points and add small modules rather than a new agent framework:

- `curator_curation/profiles/{store,updater,validation}.py`: Markdown revision lifecycle and quiet feedback updates.
- `curator_curation/llm/{gemini,gemma_client,schemas}.py`: provider boundaries and validated machine-only responses; replace the current Anthropic scorer/profile implementation.
- `curator_curation/notes/{sources,chunks,matching}.py`: read-only imports, exact-source slicing and private matching.
- `curator_curation/digests.py`: snapshot assembly and selection policy; `rank.py` remains an optional feed projection adapter.
- `curator_curation/worker.py`: durable feedback jobs and classification/matching orchestration; provider work outside DB transactions.
- `curator_curation/candidates.py` and `readers.py`: revision-aware eligibility, derived retrieval terms and compatibility with existing users.
- `curator_web/site.py`, dedicated notes/settings routes and templates: source-only reader, fixed statuses and secondary configuration access.
- `curator_core/migrations/0003_*.sql` onward: agree numbering with the pipeline owner at implementation time; update shared row contracts.
- `deploy/modal/gemma_service.py`, deployment dependency lock, `.env.example`, package metadata and `uv.lock`: runtime and provider migration.

Exact module names can change during implementation. The package ownership and data-flow boundaries are the contract.
## 9. Modal deployment and operations

Add `deploy/modal/gemma_service.py` plus pinned deployment dependencies and a small curation HTTP client. Use Modal's documented Gemma/vLLM serving pattern as the starting point [S2]. Pin a compatible Gemma checkpoint revision and runtime after one smoke benchmark; this plan does not assume a particular latest model, GPU fit, price or cold-start latency.

The service has two bounded operations: configuration revision and private chunk matching. Validate inputs/outputs independently of the inference engine. Use authenticated server-to-server access, timeouts, bounded concurrency, capped payload/token sizes and no request/response-body logging. Store credentials using the deployment's secret mechanism, never in source [S3, S4]. Load model weights once per warm container, cache weights, and measure a cold invocation and a warm invocation. Keep one warm instance only for a scheduled demo if needed and budgeted.

Keep notes inference on Gemma if Modal fails; never fall back to Gemini with private data. Leave the last valid profile/digest usable, show fixed pending/failure status and retry bounded jobs. Gemini failures similarly keep the last valid selection and record a fixed operational error.

Do not mount the Hostinger SQLite database through a Modal Volume. Modal's documented commit/reload and same-file concurrency semantics do not provide the local shared-file locking architecture used by the current database [S5]. This is an architectural incompatibility, not resolved by turning WAL on. If all application state later moves to distributed Modal workers, migrate to a transactional database accessed over a supported database protocol first.

Only the Hostinger coordinator commits database state in the initial deployment. Remote compute is stateless with respect to application state; job IDs and versioned results make retries idempotent. For sustained throughput, batch public classification and private matching separately with independent cost caps.

## 10. Incompatibilities and coordination points

| Current skeleton | Required behavior / resolution | Ownership |
| --- | --- | --- |
| Anthropic SDK, Claude models and settings | Replace with Gemini public classification and Modal Gemma configuration/notes; update lockfile and environment docs | Curation; deploy for secrets |
| `preferences` rows plus `users.prompt` as independent authorities | Markdown revisions become the authority; backfill once, use old rows only as compatibility projections and stop dual editing | Curation + core/web |
| Model reasons and generated preferences on `/me` | Remove from primary UI/API; opt-in configuration access only | Web |
| Sync feedback processing inside the HTTP request | Durable async job, serialized updates and fixed statuses | Web + curation/core |
| Cache never rescores a known post for a changed profile | Revision/content/model-aware assessments and bounded rescoring | Curation + core |
| Author block only removes new candidates | Filter previously scored/materialized items too; enforce blocks on read after a change | Curation + web |
| DID/AT URI identity and AppView rendering everywhere | Add internal identity/source adapters while retaining Bluesky compatibility | Shared core/pipeline + web |
| 24h candidate window / 48h post retention | Initial daily digest only; week/month require an agreed retention/source refresh contract | Pipeline + curation |
| Weekly digest eligibility depends on activity in last 7 days | Background delivery eligibility follows explicit subscription/cadence, not recent page visits | Curation; pipeline follows sync may need adjustment |
| Deletion cleanup only drops orphan scores on a cycle | Read-time source checks and cleanup across feed/digest/matches; edit/version events invalidate cached judgments | Shared lifecycle contract |
| Popular fallback for readers without personalized items | Standalone client shows empty/pending state; never bypass explicit user filters with fallback content | Web/curation; feed adapter explicit choice |
| `feed_items` replaced every ranking pass | Separate immutable digest editions from the optional live feed projection | Curation + core |
| Hostinger shared local DB | Keep state there and call Modal for compute; no cross-host file sharing | Deploy + curation |
| Prompt prose accepts rules such as daily quotas | Enforce list-level constraints in deterministic selection; do not assume a post scorer can enforce them | Curation |
| CI isolates each package's dependency closure | New imports/dependencies must honor that boundary; Modal deployment tested separately | Curation/web + deploy |

Retention expiry and source deletion are different events. The reader must never display a confirmed-deleted post, but keeping weekly/monthly editions readable also needs a lawful source retention/rehydration strategy agreed with the pipeline owner. Do not silently copy full social bodies into durable digest tables to evade source retention.

## 11. Implementation sequence and acceptance gates

1. **Contracts and hard boundaries.** Add public/private request types, versioned configuration storage and schema migration, legacy profile backfill, reader response DTOs without generated prose. Gate: migration preserves existing users and platform facts; no code writes source posts/notes.
2. **Quiet feedback loop.** Deploy Gemma configuration operation, durable feedback worker, atomic revision updates and secondary config access. Gate: nuanced corrections preserve unrelated preferences; rapid/concurrent feedback and retries do not lose updates; primary UI shows fixed status only.
3. **Gemini public filtering.** Replace scorer, configure models/secrets, validate batches, add versioned rescoring and hard filters. Gate: changing an interest changes selection from an identical candidate fixture; exclusions apply to old materialized items immediately.
4. **Standalone daily digest.** Add finite snapshot selection, original post rendering, dismiss/seen behavior and no popular fallback. Gate: k is a cap, duplicates are grouped, original source text/order is preserved, and the empty state is honest.
5. **Private notes matching.** Import immutable notes, derive private metadata, deploy Gemma matching, render exact chunks. Gate: every displayed passage is a verified source slice and no private payload reaches Gemini; deleting a note removes old matches.
6. **Second-platform and lifecycle integration.** Implement normalized X input and rendering with the pipeline owner, source version/delete propagation, then revisit week/month support. Gate: source adapters preserve native identities and stale/deleted content never survives through cached editions.
7. **Demo and operational checks.** Warm/cold Modal smoke test, one real Gemini batch, failure recovery and bounded costs. Gate: show feedback changing real selected posts and real note associations without generated prose entering the UI.

Do not expand to publishing, generated writing, training/fine-tuning, new agent frameworks, or a complete infrastructure migration for the first demo.

## 12. Verification and evaluation

Use deterministic unit/integration tests with fake provider responses for invariants; keep paid model evaluation separate.

- Profile fixtures: "less fundraising advice, still interested in founders' decisions"; temporary priorities; reversals; quoted instructions; private-feedback routing; invalid output; concurrent edits; retry idempotency.
- Classification fixtures: missing/duplicate/unknown candidate IDs; schema violations; invalid rule IDs; stale profile/content results; hard blocks; insufficient reply context; model/provider failure.
- Boundary tests: capture every Gemini request and assert exact allowlisted fields; reject private types; sentinel private text never appears in outbound Gemini calls or normal UI/API responses. A sentinel test supplements, not replaces, explicit types and request construction.
- Source fidelity tests: Unicode offsets, immutable note hashes, stale chunk versions, no generated text returned as an excerpt, cross-user access denied, note/source deletion propagation.
- Selection tests: top-k ceiling, threshold/empty result, topic/author diversity, conversation deduplication, delivery history, stable snapshot order and blocks after publication.
- UI integration tests: generated reason/profile fields absent from primary responses; settings require an explicit authenticated navigation; feedback status is fixed text.

Build a small labeled public-post set with relevance and exclusion judgments, plus post-to-note matches marked useful/not useful. Evaluate precision@k, useful matches@k, exclusion violations, redundancy and responsiveness to profile changes. Measure candidate recall separately from classifier quality. Track latency, tokens, provider calls, retries, Modal GPU runtime and cold starts without logging private content. Choose model IDs and batch sizes from this evaluation and the actual event account access, not assumptions in the old design document.

Run the repository's package-isolated tests and Ruff checks after implementation. A documentation-only plan does not require inference calls, deployment, credentials or application tests.

## Official references checked for this plan

These references support API/deployment choices; they do not establish platform content permissions, a chosen model's performance, or legal privacy guarantees.

- [S1] Google Gemini structured output: https://ai.google.dev/gemini-api/docs/structured-output â€” JSON Schema/Pydantic output support; validate application semantics separately.
- [S2] Modal Gemma/vLLM example: https://modal.com/docs/examples/vllm_inference â€” starting pattern for deployed Gemma inference; pin and benchmark the actual runtime/checkpoint.
- [S3] Modal web functions: https://modal.com/docs/guide/webhooks â€” serving patterns for remote compute endpoints.
- [S4] Modal secrets: https://modal.com/docs/guide/secrets â€” deployment secret injection.
- [S5] Modal Volumes: https://modal.com/docs/guide/volumes â€” commit/reload and same-file concurrent-write constraints; use for weights, not a cross-host WAL database.
