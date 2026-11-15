# Scoring and classifying posts for readers: an experiment

Run 2026-11-14. Total API spend was **$18.95** out of the $30 budget: Anthropic $14.64 (Haiku $5.34, Sonnet $4.73, Opus $4.57) and OpenRouter $4.31 (routers $1.99, decision models $2.32). Every call is logged in `data/ledger.jsonl` and cached in `data/cache/`. Those, the collected posts and the labels are not published here; see [../README.md](../README.md).

> **Correction (same day):** the first version of this report broke score ties by the order in which posts were judged. That order is correlated with relevance, so it inflated every system that outputs integer grades: the rubric prompts, the routers' rubric runs, and the Sonnet judge used as a ranker. The metric now averages over random tie-breaks (`evaluate.py`), and all tables below use it. The biggest change: Haiku with the rubric prompt is **0.81**, not 0.90. That puts it level with Haiku on the current prompt, so the "the rubric closes most of the gap to Sonnet" finding was an artifact. Systems with continuous scores (Sonnet/Opus/Haiku on the current prompt, embeddings, decision models) barely moved.

## Setup

- **Corpus:** 11,502 unique English Bluesky posts (≥40 chars), from about 10 minutes of the live Jetstream firehose (`collect.py`).
- **Readers:** 8 synthetic profiles written in the curator's markdown format, each with interests and explicit exclusions (`readers.py`). They cover ML research, US policy, climate/energy, indie gamedev, books, biomedicine, urbanism and software engineering.
- **Reference labels:** Claude Opus 5.5 at high effort graded each (reader, post) pair 0–3 (3 = "squarely in their interests and substantive"). Pairs come from TREC-style pooling: for each reader, the union of the top 30 from each of three different systems plus 40 random posts. That gives 896 judged pairs, of which 191 are relevant (grade ≥2).
- **How reliable are the labels:** a second judge (Sonnet 5.5, high effort) agrees with Opus at quadratic-weighted κ = 0.90, with 94% agreement on relevant vs not (κ = 0.81). As a ranker, Sonnet's integer grades score NDCG@10 0.89, because ties within a grade cost it. Sonnet's continuous scores reach 0.94. Treat anything around 0.93 or above as at the noise ceiling.
- **Metrics:** NDCG@10, P@10 (share of grade ≥2 posts in the top 10) and ROC-AUC (relevant vs not), averaged over readers. Ties are broken at random, averaged over 20 draws. 95% confidence intervals come from a paired bootstrap over the judged posts.

## Approaches tested

| ID | Approach | What runs per reader |
|---|---|---|
| A | Per-reader LLM batch scoring with **the curator's current prompt** (`quiet-v1`), 20 posts per call | an LLM call per 20 (reader, post) pairs |
| A′ | Same, but with an explicit **0–3 rubric prompt** (the judge prompt) | same |
| B | Local embedding similarity between the profile and each post (bge-small / bge-base, fastembed on CPU) | a dot product |
| C | **Reader-independent tagging, once per post** (Haiku): 1–3 topics from a fixed 49-topic list, a substance score and bad-content flags. Each reader is mapped once to topic weights (Sonnet, $0.03 for all 8). | arithmetic only |
| D | Cascades: a cheap prefilter keeps the top K posts, then an LLM reranks them per reader | an LLM on K posts only |

## Results: relevance ranking

| System | NDCG@10 (95% CI) | P@10 | AUC | $ per 1k pairs | p50 latency per call |
|---|---|---|---|---|---|
| Opus 5.5, low effort, current prompt | **0.982** (0.95–0.99)\* | 0.98 | 0.989 | $1.26 | 5.9 s |
| Sonnet 5.5, low effort, current prompt | **0.936** (0.89–0.96) | 0.94 | 0.977 | $0.63 | 4.1 s |
| Cascade: Haiku top-200 → Sonnet | 0.936 | 0.94 | 0.968 | ≈$0.03 + 1.7% of pairs on Sonnet | |
| Cascade: topic tags top-500 → Sonnet | 0.928 (0.88–0.96) | 0.94 | 0.936 | shared tagging + 4.3% of pairs on Sonnet | |
| **Best decision models (see below):** Gemini Decision 1, Perplexity Decider v1.3, Liquid d1-ultra | **0.907–0.920** | 0.91–0.95 | 0.97 | **$0.016–0.047** | **0.24–0.56 s per pair** |
| Sonnet-high judge, integer grades | 0.892 | 0.89 | 0.960 | $1.21 | 4.8 s |
| Haiku 5.5, low/medium effort, current prompt | 0.80–0.84 (5 runs) | 0.82–0.86 | 0.94 | $0.03 | 2.9 s |
| Haiku 5.5, high effort, rubric prompt (A′) | 0.814 / 0.798 (2 runs) | 0.84 | 0.947 | $0.06 | 6.9 s |
| Haiku 5.5, current prompt, each post batched with 19 random firehose posts | 0.707 (0.63–0.78) | 0.68 | 0.896 | $0.03 | 2.9 s |
| Topic tags + substance + flag penalty (C) | 0.625 (0.55–0.71) | 0.65 | 0.849 | $0.055 per 1k *posts*, shared by all readers | 7 s / 25 posts |
| Topic tags only | 0.453 | 0.43 | 0.820 | same | |
| Embeddings (best: bge-base, whole profile) | 0.31–0.43 | 0.25–0.30 | 0.67–0.73 | free (CPU) | 40 ms per post† |

\* Opus scoring is checked against Opus labels, so its lead is partly the judge agreeing with itself. Count it as "at ceiling", not as better than Sonnet.
† fastembed on this box was unexpectedly slow, probably from thread oversubscription. bge-small normally runs at about 1–3 ms per post on CPU.

**How good each cheap prefilter is: recall of relevant posts when keeping the top K of 11.5k**

| Prefilter | K=100 | K=200 | K=500 | K=1000 |
|---|---|---|---|---|
| Embeddings (bge-small) | 0.45 | 0.55 | 0.69 | 0.81 |
| Topic tags + substance (C) | 0.83 | 0.90 | 0.94 | 0.96 |
| Haiku per-reader (A) | 0.89 | 0.98 | 1.00 | 1.00 |

### Findings

1. **Sonnet with the current prompt is at the measurement ceiling.** Haiku with the current prompt is clearly worse (NDCG −0.10 to −0.14, statistically significant).
2. **The rubric prompt doesn't help Haiku** (0.80–0.81 vs 0.80–0.84). Integer grades also produce many ties near the top, which a ranker can't resolve. Continuous scores work better: either the current prompt's 0–1 relevance, or a decision model's expected grade. Effort (low vs medium) changed nothing. Haiku's run-to-run noise is about ±0.03 NDCG.
3. **Batch context skews the scores.** Posts are scored relative to what else is in the batch. When a relevant post sits among 19 random firehose posts, Haiku's ranking drops to 0.71 NDCG. With 25% filler it holds at ~0.81, and Sonnet barely moves (0.94 → 0.92). Decision models score each (reader, post) pair on its own, so they avoid this by construction.
4. **Embeddings are a poor relevance signal on short posts.** They fail on substance and on exclusions: subtracting a penalty for the "not interested" bullets didn't help. Their prefilter recall is also weak (69% at K=500).
5. **Tagging posts once by topic is a good shared prefilter** (90% recall at the top 200 of 11.5k, 94% at the top 500) but a mediocre final ranker. It costs $0.055 per 1k posts no matter how many readers there are.
6. **A cascade matches full per-reader Sonnet quality at a few percent of the cost.** Both "topic tags → Sonnet on the top 500" and "Haiku → Sonnet on the top 200" land at 0.93–0.94.
7. **Decision models (OpenRouter's Decisions API) are the best cheap option.** The top three reach 0.91–0.92, between Haiku and Sonnet and significantly better than Haiku. They cost $0.02–0.05 per 1k pairs, run at a quarter of a second per pair, and return calibrated probabilities. See the decision-models section.

## Results: bad content (clickbait, ragebait, misinformation, spam)

There are two measurements, because bad content is too rare in a random sample to evaluate on its own:

**1. A seeded set with known labels** (`synth.py`, 160 posts written by Sonnet). It has 20 each of clickbait, misinformation, ragebait and spam. It also has 80 *hard negatives*: sober news, strongly worded but good-faith opinion, honest self-promotion (paper or game launches) and posts that correctly debunk a myth while mentioning it.

| Classifier | Any-bad precision / recall | False positives on hard negatives | Misinformation AUC |
|---|---|---|---|
| Regex heuristics | 0.79 / 0.29 | 7% | 0.52 |
| **Haiku tagger (same pass as topic tagging)** | **0.97 / 0.93** | 3% (2 honest promos) | 0.98 |
| Sonnet tagger | 1.00 / 0.99 | 0% | 0.99 |
| Opus judge | 1.00 / 1.00 | 0% | 0.99 |

Precision for each flag separately is lower (for example, clickbait P = 0.53–0.65). That's because the categories overlap: many misinformation and ragebait posts are also clickbait. It isn't confusion with the benign posts.

**2. Real firehose posts.** Opus labeled 350 real posts: 250 random plus 100 that Haiku scored as most suspicious.

- **How common each kind is among random English posts:** ragebait 6.4%, spam/promo 6.4%, clickbait 0.8%, misinformation 0.8%, low-effort 22%.
- **Haiku tagger vs the Opus labels:**

| Flag | Positives in sample | AUC | Average precision | Precision / recall at threshold 0.5 |
|---|---|---|---|---|
| Spam/promo | 68 | 0.99 | 0.98 | 0.95 / 0.93 |
| Ragebait | 52 | 0.98 | 0.86 | 0.62 / 1.00 |
| Clickbait | 11 | 0.98 | 0.63 | 0.47 / 0.64 |
| Misinformation | 7 | 0.91 | 0.75 | 0.29 / 0.86 |

  The 0.5 threshold is too low for ragebait; tune it on these labels. The regex heuristics score AUC 0.49–0.64 here, close to useless.
- **Caveat:** the enriched part of the sample was chosen by Haiku's own scores, which flatters its recall a little. The misinformation numbers rest on only 7 real positives.

### How I'd measure this properly

- **Keep both kinds of evaluation.** The seeded set (with hard negatives) measures recall and false-positive rate. Labels on a random sample measure how common each problem is and how precise the flags are in practice. Grow the seeded set from public corpora rather than only LLM-written posts. For clickbait, Webis Clickbait-17. For misinformation, LIAR / FEVER / MultiFC claims rewritten as posts, plus the Community Notes public dataset (posts with "misleading" notes are real positives). For spam, the bot and spam accounts in Bluesky's moderation labels.
- **Treat misinformation differently.** A model judging only the text can spot well-known myths and conspiracy framing; that's what was measured here. It can't verify fresh claims. For a feed, the useful signals are the "unverified factual claim" flag plus *grounding*: web search on flagged posts only (rare, so cheap), domain-reputation lists for link cards, and subscribing to Bluesky labelers (Ozone). Measure the grounded check separately on recent fact-checks.
- **Measure what it does to the feed:** the share of flagged posts in each reader's top 10 before and after filtering, and the share of relevant posts lost by filtering (the false-positive cost). In this run, honest self-promotion was the main false-positive risk.
- **Keep the flags separate from the reader.** These flags don't depend on the reader, so compute them once per post in the tagging pass. In this run they came free with the topic tags, at $0.055 per 1k posts.

## Cost and latency at scale (illustrative)

Throughput at concurrency 24: Haiku scored 92k pairs in 453 s, about 200 pairs/s.

Scenario: 1,000 readers × 500 candidate posts per reader per day (For You snapshots plus follows) = 500k pairs/day.

| Design | $/day |
|---|---|
| Haiku, current prompt (A) | ~$15 (NDCG ~0.82) |
| Haiku, rubric prompt, high effort (A′) | ~$30 (NDCG ~0.81) |
| **Decision model per pair: Liquid d1-ultra / Gemini Decision 1** | **~$8 / ~$23** (NDCG 0.91–0.92) |
| Perplexity Decider v1.2 per pair | ~$4 (NDCG 0.90) |
| Sonnet, current prompt | ~$315 (NDCG 0.94) |
| Topic tags + flags on each unique candidate (~200k/day, shared) + Sonnet on each reader's top 20% | ~$11 + ~$63 = **~$75** (0.93) |
| Decision-model flags on each unique candidate + decision-model relevance on every pair + Sonnet on each reader's top 50 | ~$4 + ~$8 + ~$32 ≈ **~$45** (not measured end to end) |

For comparison, the whole English firehose (filtered as above) is about 1.6M posts/day. Tagging all of it once would cost about $88/day in total, whatever the number of readers. That's the only way per-post cost stays flat as you add readers. Per-reader LLM scoring of the whole firehose (about $50 per reader per day even on Haiku) isn't viable.

Neither prompt is cacheable as written: the profile plus instructions are below the minimum cacheable prefix. A shared long system prompt with few-shot examples could be cached and would cut input cost further.

## Recommendation for the curator

1. For per-reader relevance, replace the Gemini batch call with a **decision model, one (reader, post) pair per request**, using a 4-level `score` question built from the rubric. Liquid d1-ultra and Gemini Decision 1 were the best here. That's about Haiku's cost or less, roughly 10× faster, independent of batch, and 0.1 NDCG better. Pin the model version, since probabilities shift between versions.
2. Add a reader-independent pass once per post: decision-model `noul` flags for clickbait, ragebait, misinformation, spam and low effort (d1-ultra, Jev 1.14 or Perplexity v1.2, ~$0.01–0.03 per 1k posts), plus topic tags if they're needed for recall. Use the flags as filters or penalties.
3. Keep an LLM (Sonnet) only for the final top-N per reader, where you need its quality and want prose reasons for the digest.
4. Keep this harness as a regression eval: `analysis.py` and `quality_eval.py`, with the Opus labels as the reference set. Re-run it after any change to the prompt or model.

## Caveats

- The readers are synthetic, and the "truth" is a Claude judge, not human clicks. The tested models may share the judge's biases; Sonnet agreeing with Opus at κ 0.90 bounds this but doesn't rule it out. Before trusting the absolute numbers, label ~100 pairs by hand, or use real engagement as the signal.
- The corpus is 10 minutes of the firehose, so there was little ML content (only 9 relevant posts for that reader). Real For You candidates are pre-personalized and much denser in relevant posts than random firehose posts.
- Only the cascades that reuse Sonnet's pool scores could be evaluated. Unjudged posts were treated as not relevant, which is the standard pooling assumption.

## Files

- `collect.py`: Jetstream sampler
- `readers.py`: reader profiles
- `score.py`: approach A / A′ scorer
- `embed.py`: approach B
- `tag.py`: approach C (topics + flags), plus reader→topic mapping
- `gold.py`: reference labelers (relevance and bad content)
- `synth.py`: seeded bad-content set
- `build_pool.py`: corpus and pooling
- `evaluate.py`: metrics and bootstrap
- `analysis.py`: final ranking tables
- `quality_eval.py`: bad-content tables
- `decide.py`, `dec_report.py`: Decisions API models; `tiecheck.py`: tie-break check
- `results/analysis.json`, `results/quality_results.json`: aggregate metrics behind the tables

## Decision models: Jev and 25 others on the Decisions API

`POST https://openrouter.ai/api/alpha/decisions` serves *decision models*. You send a `state` (here, the reader profile and one post) plus typed questions, and get typed answers with probabilities. There's no generated text or reasoning. Question types include `score` (an ordered scale, returning the expected position and a probability per level), `noul` (P(yes)) and `choice`. Billing is input tokens only (~$0.01–0.20 per M); output is free. The client is `decide.py`, the report script `dec_report.py`. 41 decision models are listed. I ran 26 general-purpose ones on 37k requests, costing $2.32 in total.

Question design (identical for every model):
- **Relevance:** one request per (reader, post). It asks a `score` question over the four rubric levels (0–3, same wording as the Opus judge's rubric) and a `noul` question, "would this reader be glad to see this in their digest?". Systems rank by the expected grade (`grade`), by P(yes) (`want`), or by the mean of their percentile ranks (`grade+want`).
- **Bad content:** one request per post, with five `noul` questions (clickbait, ragebait, misinformation, spam/promo, low effort). Each uses the same definitions as the LLM tagger and gives explicit "false" criteria for the benign look-alikes.

### Relevance (896 Opus-graded pairs; `grade` unless noted)

| Model | NDCG@10 | Δ vs Haiku-rubric (95% CI) | P@10 | AUC | QWK (argmax) vs Opus | $ / 1k pairs | p50 / p90 per request |
|---|---|---|---|---|---|---|---|
| *Sonnet 5.5, current prompt (LLM reference)* | *0.936* | *+0.07..+0.15* | *0.94* | *0.977* | — | *$0.63* | *4.1 s per 20* |
| **Google Gemini Decision 1** (grade+want: 0.920) | **0.915** | **+0.05..+0.16** | 0.91 | 0.972 | 0.85 | $0.047 | 0.24 / 0.40 s |
| **Perplexity Decider v1.3** (free tier) | **0.915** | +0.04..+0.16 | 0.94 | 0.971 | 0.85 | free ($0.02/M paid v1.2) | 0.29 / 0.51 s |
| **Liquid d1-ultra** (grade+want: 0.912) | **0.907** | +0.04..+0.14 | 0.95 | 0.971 | 0.86 | **$0.016** | 0.56 / 1.10 s |
| Perplexity Decider v1.2 | 0.895 | +0.01..+0.13 | 0.93 | 0.964 | 0.83 | $0.008 | 0.28 / 0.45 s |
| HeyGen Decision 1 | 0.876 | −0.01..+0.10 | 0.88 | 0.961 | 0.84 | $0.226 | 0.41 / 0.76 s |
| Nace Drex Max | 0.875 | +0.01..+0.12 | 0.88 | 0.965 | 0.80 | $0.028 | 1.34 / 1.77 s |
| Baseten Mammoth 1 (27B) | 0.867 | −0.01..+0.11 | 0.90 | 0.966 | 0.84 | $0.045 | 0.26 / 0.29 s |
| Liquid d1.1 (want: 0.893) | 0.865 | +0.00..+0.11 | 0.88 | 0.955 | 0.82 | $0.008 | 0.45 / 0.78 s |
| Morph Reflex, Microsoft Decision 1 | 0.846 | ≈0 | 0.86–0.88 | 0.95 | 0.80–0.82 | $0.014–0.018 | 0.37–0.42 s |
| TypeSafe Jev 1.13 | 0.838 | −0.05..+0.06 | 0.88 | 0.955 | 0.79 | $0.027 | 0.22 / 0.26 s |
| OpenAI GPT-6 Luna Decisions | 0.832 | −0.05..+0.09 | 0.88 | 0.933 | 0.76 | $0.055 | 0.23 / 0.35 s |
| TypeSafe Jev 1.14, 10 posts per request | 0.820 | −0.07..+0.06 | 0.82 | 0.948 | 0.77 | $0.011 | 0.26 / 0.39 s |
| *Haiku 5.5 rubric / current prompt* | *0.814 / 0.80–0.84* | — | *0.84* | *0.946* | *0.85* | *$0.06 / $0.03* | *6.9 / 2.9 s per 20* |
| Cloudflare Clef / Clef Omni, Nace Drex v2.0, Baseten Elephant 1, Perplexity Flash v1.3 | 0.81–0.83 | ≈0 | 0.80–0.90 | 0.93–0.95 | 0.72–0.76 | $0.016–0.11 | 0.2–1.2 s |
| **TypeSafe Jev 1.14** (single post) | 0.778 | −0.08..+0.04 | 0.81 | 0.957 | 0.79 | $0.027 | 0.23 / 0.26 s |
| Perplexity Flash v1.2, Kev 4B, Clef Flash, Upstage Solar Decide, Together Tev1 4B | 0.73–0.79 | ≤0 | 0.69–0.84 | 0.88–0.92 | 0.51–0.75 | $0.004–0.06 | 0.2–6.4 s |
| Upstage Solar Decide Flash, Baseten Joey 1 (2B) | 0.54–0.64 | significantly worse | 0.56–0.60 | 0.80–0.87 | 0.40–0.58 | $0.005–0.06 | 0.25–0.72 s |

- **The top three decision models beat every cheap LLM setup tested.** Gemini Decision 1, Perplexity Decider v1.3 and Liquid d1-ultra all beat Haiku with significant intervals, and come within ~0.02–0.03 of Sonnet at 2–8% of its cost. They answer in about 0.25–0.6 s per pair and need no JSON parsing.
- **Jev is mid-pack on relevance.** Jev 1.13 (0.84) beat 1.14 (0.78), and 10 posts per request (0.82, 2.5× cheaper) beat one post per request for 1.14. Expect thresholds and quality to shift between model versions; pin a version.
- **Continuous expected grades avoid the tie problem**, which is part of why these models outrank integer-graded LLM output. Grade agreement with Opus (QWK ~0.85) is the same as Haiku's, so the decision models aren't better at the grade itself. The gain comes from ranking with the probabilities.
- Combining `grade` and `want` helps a little for Gemini and d1-ultra (+0.005) but isn't consistent; `grade` alone is a good default.

### Bad content (AUC; synthetic set = 160 seeded posts, real = 350 Opus-labeled Jetstream posts)

| Tagger | synth any-bad | real clickbait | real ragebait | real misinfo (n=7) | real spam | real low-effort | real mean of 4 bad | $ / 1k posts | p50 |
|---|---|---|---|---|---|---|---|---|---|
| **Liquid d1-ultra** | 1.000 | 0.997 | 0.988 | 0.992 | 0.999 | 0.889 | **0.994** | $0.022 | 0.68 s |
| **Perplexity Decider v1.2** | 1.000 | 0.987 | 0.977 | 0.991 | 1.000 | **0.921** | 0.989 | **$0.011** | 0.29 s |
| **TypeSafe Jev 1.14** | 1.000 | 0.993 | 0.983 | 0.990 | 0.991 | 0.855 | 0.989 | $0.033 | 0.23 s |
| Baseten Mammoth 1 | 1.000 | 0.990 | 0.971 | 0.991 | 0.997 | 0.878 | 0.987 | $0.061 | 0.27 s |
| TypeSafe Jev 1.13 | 1.000 | 0.990 | 0.985 | 0.982 | 0.988 | 0.838 | 0.986 | $0.033 | 0.23 s |
| Liquid d1.1 | 1.000 | 0.992 | 0.985 | 0.971 | 0.992 | 0.870 | 0.985 | $0.011 | 0.48 s |
| Jev router (DeepSeek Flash, LLM) | 1.000 | 0.986 | 0.984 | 0.974 | 0.995 | 0.868 | 0.985 | $0.125 | 18 s |
| Most others (Gemini, GPT-6 Luna, Clef, HeyGen, Drex, Morph, Perplexity v1.3) | 0.996–1.000 | 0.97–0.99 | 0.96–0.98 | 0.96–0.99 | 0.98–1.00 | 0.84–0.91 | 0.97–0.98 | $0–0.45 | 0.2–1.5 s |
| Parallel Decisions Fast (web-grounded) | 0.997 | 0.974 | 0.964 | 0.968 | 0.991 | 0.854 | 0.974 | $1.00 | 0.63 s |
| *Haiku 5.5 tagger (LLM)* | *0.998* | *0.977* | *0.981* | *0.905* | *0.988* | *0.803* | *0.963* | *$0.055* | *~3 s per 25* |
| Small models (Kev 4B, Tev1 4B, Solar Decide, Joey 1) | 0.92–0.99 | 0.92–0.96 | 0.90–0.97 | 0.86–0.97 | 0.90–0.96 | 0.47–0.81 | 0.91–0.95 | | |

- **Bad-content flags are easy for decision models.** Most mid-size and large ones match or beat the Haiku LLM tagger, at a fifth to half its cost and 10× lower latency. d1-ultra, Perplexity v1.2 and **Jev 1.14** are the best buys; Jev is the fastest of them. Tagging the whole English firehose (~1.6M posts/day) would cost about **$18–53/day**, against ~$88 with Haiku.
- **Web grounding didn't help on this set** (Parallel). Its misinformation positives are well-known myths, which text-only models already catch. Grounding should still matter for fresh claims, but that needs a test set of recent fact-checks.
- Google Gemini Decision 1 rejected 9 of the 350 real posts as "sensitive" (NSFW) with an HTTP 400. Production would need a fallback model for rejected posts.
- **Same caveats as before:** misinformation has 7 real positives; part of the real sample was chosen by Haiku's scores; the labels come from Opus.

### What this changes

For both tasks, decision models are the right tool at this price point. Measured on this harness, with recall, the per-reader ranking and the final LLM step still to be tested end to end, a curator pipeline would look like this:

1. **Tag each post once** with five `noul` flags on d1-ultra or Jev 1.14 (~$0.02–0.03 per 1k posts).
2. **Score each (reader, candidate) pair** with a `score` question on Liquid d1-ultra or Gemini Decision 1 (~$8–23/day at 1,000 readers × 500 pairs).
3. **Send each reader's top 30–50 posts to Sonnet** for the final cut and the digest reasons.

Next experiments:
- Batch several posts per request for the top relevance models. It helped Jev 1.14 on both quality and cost.
- Tune thresholds per flag on the Opus labels.
- Run an end-to-end cascade: decision-model shortlist → Sonnet.
