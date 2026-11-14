# Design: per-user, LLM-curated Bluesky feeds

For repo layout and who owns what, see README.md and packages/core/README.md.

Status: groundwork, as of 2026-11-14. Everything below runs end to end: live Jetstream ingest,
a real Claude scoring batch, ranking, the feed skeleton endpoint, and the start of the OAuth
flow (PAR against pds.ilia.fi). Not yet exercised: a real Bluesky app request with a service
JWT, a completed OAuth callback, and production deployment.

## What we're building

A reader writes, in plain language, what they want to see. They pin **one** feed in the
Bluesky app, and it is different for every reader. A separate website is where they write the
description, give feedback, see why posts were picked and, later, read the feed outside the
app.

The key protocol fact that makes this cheap: **a feed generator learns who is asking.** The
AppView attaches a service-auth JWT signed by the reader's key to `getFeedSkeleton`. So one
published feed record serves a personalised feed to everyone, and nobody needs a feed per user.

## Delivery options

| Option | Pros | Cons |
|---|---|---|
| **A. Feed generator only** | Native in the app; protocol handles auth | No place to configure; feedback is limited to "show more/less" |
| **B. Website reader only** | Full UI control (reasons, digest view, grouping) | Users must leave the app; needs OAuth plus your own post rendering |
| **C. Both, sharing one core** *(chosen)* | App for reading, site for steering; same ranked list | Two surfaces to maintain |

## Pipeline

```
Jetstream (posts, likes, reposts)          ~40 posts/s, ~250 likes/s, measured 2026-11-14
   │   ingest: INSERT posts (+FTS5), bump like/repost/reply counters, 48h retention
   ▼
SQLite (WAL) ── ingest-owned: posts, posts_fts, follows, follow_sync, kv
   ▲             curation-owned: users, preferences, scores, feed_items, interactions
   │                                   │
   │ curate worker, every 10 min:      ▼
   │ 1. candidates  (cheap, SQL):  follows' posts + FTS on search_terms + popular
   │ 2. score       (LLM):  Claude, batches of 40, structured output 0-10 plus a reason
   │ 3. rank        (cheap):  score × recency decay × log-engagement × author boosts,
   │                          then a greedy per-author diversity penalty → feed_items
   │
web: getFeedSkeleton reads feed_items (no LLM in the request path)
     sendInteractions → interactions → few-shot "more/less like this" in the next scoring prompt
     site: description, freeform feedback → structured preferences (the doorss pattern)
```

### Why candidates come before the LLM

At about 3.5M posts/day (≈1.8M English), scoring the firehose once per reader is out of the
question at any price. Recall has to be cheap and the LLM spends its effort on precision. The
three recall sources in `curator_curation/candidates.py` are a starting point, roughly in order of value:

1. **Follows**: what the reader opted into. Ingest syncs them from the public AppView every 6h.
2. **FTS search terms**: the profile LLM turns "I like compilers" into `search_term`
   preferences, and SQLite FTS5 recalls matches for free. Keyword recall is noisy: the live
   smoke test matched "ai" in a Portuguese Eurovision lyric, which the scorer correctly gave 0.
   Noise is fine at this stage; precision is the scorer's job.
3. **Popular**: likes + 2×reposts ≥ 25, for serendipity.

Better recall sources, in order of how much they'd likely help:
- **Likes by follows** (second-degree): needs ingesting `like` records by DID for followed
  accounts. Jetstream already delivers them; today we only count them.
- **Embedding similarity** to the reader's description and their liked posts. Anthropic has no
  embeddings endpoint, so use a local model (MiniLM/bge-small via `fastembed`, CPU-friendly;
  embed only candidate-pool posts, not the whole firehose) or Voyage.
- **Co-like collaborative filtering**, the spacecowboy "For You" approach (one Go binary plus
  SQLite, Jetstream, 72k DAU from a home PC). Strong, but it's a project of its own.

### LLM usage

- `curator_curation/llm/scorer.py`: `client.beta.messages.parse` with a Pydantic schema, `effort=low`, and
  server-side refusal fallbacks (`fallbacks="default"`, beta `server-side-fallback-2026-07-01`).
  This is on by default; remove it if you'd rather a refused batch just be skipped.
- Prompt-cache layout: frozen instructions, then the per-reader profile (cache breakpoint),
  then the varying posts. The smoke test showed `cache_write=769`, so the per-reader prefix is
  cached across the batches in a cycle.
- Post text is untrusted. It is wrapped in `<post>` tags, and the model is told to ignore
  instructions inside them. Scores are numbers clamped by the schema, so an injection can at
  worst promote one post in one reader's feed.

## Cost: the main decision for you

Measured on one live batch: about **$0.001 per post scored** with `claude-opus-5-5` at low
effort. About 30 output tokens per post go to the reason string, and output tokens dominate.

| Per reader per day | Opus 5.5 (default) | Haiku 5.5 |
|---|---|---|
| 1,000 candidates | ~$1 | ~$0.03 |
| 3,000 candidates | ~$3 | ~$0.08 |

Ways to cut cost, roughly in order:
1. Switch `SCORER_MODEL` to `claude-haiku-5-5` for the bulk pass. Optionally rescore only the
   top ~50 with Opus.
2. Drop or shorten `reason`. Output tokens are 5× the price of input tokens.
3. Tighten candidates: skip replies to non-follows, skip posts with no text, embed-prefilter.
4. Use the Message Batches API (50% off). Results take minutes to hours, which fits a 10-min
   cycle with some lag. Batches don't accept `fallbacks`.
5. Share scores between readers with similar profiles. Only worth it at many users.

**Abuse guard:** anyone can pin a published feed. Only readers who signed in on the site and
wrote a description (`users.prompt != ''`) get LLM curation. Everyone else gets the no-LLM
`__global__` popular feed. Before opening sign-ups, add a per-reader daily candidate cap and
an allowlist or invite code.

## Auth

- **Feed requests:** service JWT, verified in `curator_web/feedauth.py` with `atproto`'s
  `verify_jwt_async`, checking `aud == did:web:<host>`, `lxm`, and the signature resolved
  from the reader's DID document.
- **Website:** atproto OAuth (PAR + PKCE + DPoP) via `atproto` 0.0.75's `AsyncOAuthClient`.
  That client **was released today**, so expect rough edges. It is a loopback client in dev
  and a confidential client (`private_key_jwt`, 180-day refresh) in prod.
  - Scope is `atproto` only, for identity. Follows are public.
  - To read the reader's timeline or likes later, add the read-only permission set:
    `include:app.bsky.authViewAll?aud=did:web:api.bsky.app%23bsky_appview`.
  - Sessions live in `JsonFileStore`, which is fine for one web process. Move them to SQLite
    before scaling out.
- **Publishing the feed record** uses an app password once (`scripts/publish_feed.py`).

## Stack choice

The project uses Python, uv, FastAPI and SQLite, matching doorss and bluesky_playground.
TypeScript is the more mature atproto stack (`@bsky/jetstream`, `@atproto/lex-server`,
`@atproto/oauth-client-node`). Python is now viable for every piece we need, and Claude/LLM
work is more pleasant there. If ingest ever becomes the bottleneck, port only the ingester to
Go (indigo); the SQLite contract stays the same.

**SQLite sizing:** about 780 B/post including FTS, so about 1.4 GB/day for English only and
about 2.8 GB at 48h retention. Delete churn is high, so plan for a weekly `VACUUM` or
`auto_vacuum=INCREMENTAL`. Move to Postgres when there are several curate workers or the web
process sees write locks.

## Policy

- Honour deletes (done: post deletes come from Jetstream and remove the row and its FTS
  entry).
- Publish a privacy policy (stub at `/privacy`, linked from `describeFeedGenerator`).
- Bluesky has no explicit rule on storing post text for ML. Don't train models on it.
  Short-retention scoring is the conservative reading.
- Parts of the Bluesky community are hostile to AI (Attie became the second most-blocked
  account). Be upfront that an LLM ranks the feed.

## Prior art worth reading before going further

- **MySky / GreenEarth** (open source, github.com/greenearth-social): candidate generation,
  then signals (including an LLM relevance scorer for a user prompt), then MMR
  diversification. Closest to this design.
- **spacecowboy "For You"**: https://atproto.com/blog/serving-the-for-you-feed
- **Attie**: Bluesky's own natural-language feed builder.
- **Graze**: no-code builder, now owned by Flipboard.

## Next steps (suggested order)

1. Deploy (see `deploy/vps_deployment.md`), publish the feed, pin it, and confirm a real
   app request authenticates. That's the one path the smoke test couldn't exercise.
2. Choose the scorer model and add a per-reader daily budget.
3. Add likes-by-follows recall, then embeddings.
4. Build a better reader UI on the site: grouped digest, reasons, "why not this?".
5. Build an eval: the reader labels about 200 posts, and you measure scorer agreement per
   model and effort level before changing prompts.
