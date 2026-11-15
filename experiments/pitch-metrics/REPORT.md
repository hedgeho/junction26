# Agora feed: pitch numbers

Measured 2026-11-15 against the deployed pipeline at `c573ea1`. The scorer questions, thresholds, triage and digest
rules are unchanged since `9fdf92e`; the latest commits change only candidate recall and profile seeding.
Spend for this report: OpenRouter $6.51 (Gemini Decision 1, 103k requests); Anthropic $2.69 (Opus 5.5, high effort).
Anthropic total across all experiments: $17.33.

## Headline numbers

| # | Claim | Number | Basis |
|---|---|---|---|
| 1 | **9 in 10 digest posts are relevant to the reader** | **92%** (69/75, 95% CI 84–96%) vs **0.6%** of the raw firehose | 8 reader profiles × 11.5k real Bluesky posts, full production pipeline, blind Opus grades |
| 2 | **No junk in the digest** | **0%** clickbait/ragebait/misinformation/spam (0/75, CI 0–5%) vs **15%** of the firehose | Same digests, Opus content labels |
| 3 | **Beats your Bluesky For You** | AI-safety reader: **0 of 40** For You posts relevant; Agora with the current scorer **17 of 18** (94%) across 3 real readers | Production DB snapshot, blind and shuffled Opus grades against each reader's own profile |
| 4 | **Cheap and fast** | **$0.00006** and **0.29 s** to score one post for one reader; a refreshed digest is ready in **~7.5 s** | OpenRouter list price, 103k requests (p90 0.48 s); production timing records |

Pitch line: *"We read about 2.5 million Bluesky posts a day so you read 10, and 9 of those 10 are what you asked for."*
The firehose sample is about 105k English posts/hour, measured on the production ingest.

## Further options

- **Highlight rate:** 28% of digest posts are graded "squarely in your interests and substantive" (grade 3). That is 0% for the firehose.
- **Against other approaches** (top 10 per reader over the same 11.5k posts):

  | System | Relevant | Unrelated | Junk |
  |---|---|---|---|
  | **Agora digest** | **92%** | 1% | 0% |
  | Agora without content flags | 95% | 0% | 4% |
  | Gemini relevance grade alone (no gate/flags/diversity) | 90% | 1% | 5% |
  | Haiku 5.5 LLM, per reader | 71% | 12% | 2% |
  | Embedding similarity (bge-small) | 25% | 25% | 10% |
  | Random firehose | 0.6% | 94% | 15% |

  The flags trade about 3 points of relevance for junk going from 4–5% to 0%.
- **Near the quality of a top LLM at a fraction of the cost** (earlier harness, NDCG@10): the current scorer reaches 0.91, and Sonnet 5.5 scoring every pair reaches 0.94, at about 10× the cost. Two LLM judges agree with each other at QWK 0.90, which is the measurement ceiling.
- **Moderation at firehose scale:** flags hide 9.5% of all firehose posts and downrank another 5.6%. Free screening (moderation labels, copy-paste campaigns, high-rate accounts, tag spam) drops about a quarter of real candidates before any model call.
- **Hard to game:** prompt-injection suffixes moved 0 of 48 irrelevant posts into the digest. Opposing viewpoints are not penalised (+0.02 to +0.12); contempt is (−0.2 to −0.3).
- **Cost per reader:** at most $0.32/day at the production cap of 5,000 scored posts. The content flags are computed once per post and shared by all readers.
- **Fun demo:** the "green birds only" reader. The production log shows the scorer passed 2 of 220 candidates.

## Method

**Synthetic readers (claims 1, 2 and the comparison table).** The posts are 11,502 English Bluesky posts from the live
Jetstream. The readers are 8 profiles in the curator's format with interests and exclusions, from
`../feed-scoring/readers.py`. Every (reader, post) pair, 92k in all, was scored with the exact production Decisions
questions: grade, want, substance and opportunity, one post per request. Every post was flagged once with the
production triage questions. `digest_sim.py` then applies `digests.build`:
- the gate: want ≥ 0.5 and grade/3 ≥ 0.4;
- hide and downrank thresholds;
- value = 0.65·rel + 0.25·sub + 0.1·opp;
- at most 2 posts per author, plus text and link dedup;
- 10% serendipity.

The digests were graded blind by Opus 5.5 at high effort with the reference rubric. Most posts were already in the
896-pair reference pool, and 41 new pairs were graded. Relevant means grade ≥ 2. Junk means any of clickbait,
ragebait, misinformation or spam, as judged by Opus.

Left out of the simulation: recency decay (all posts are from the same 10 minutes), followed-author slots (synthetic
readers follow no one) and conversation dedup (no thread ids in the sample).

**Real readers (claim 3).** This uses a read-only snapshot of the production DB from 2026-11-15 00:06 UTC, with 3
readers. For each reader, Opus graded against the reader's current public profile, shuffled and with no source
information:
- every post Agora ever delivered to them (80);
- their stored Bluesky For You posts (up to 40);
- the latest 40 top-level posts from accounts they follow;
- 40 random firehose posts.

Images and link cards were attached.

| Source | n | Relevant | Unrelated | Junk |
|---|---|---|---|---|
| Agora, current scorer (`decision-v2`, from 23:00 UTC) | 18 | **94%** | 0% | 0% |
| Agora, all digests incl. older scorers | 80 | 79% | 4% | 4% |
| Bluesky For You (AI-safety reader) | 40 | 0% | 82% | 5% |
| Following, chronological | 46 | 4% | 74% | 0% |
| Random firehose | 120 | 6% | 87% | 22% |

## Caveats (say these if asked)

- **The judge is Opus, not people,** and 5 of 8 profiles are synthetic. The claims are about agreement with a strong
  judge reading the reader's own stated profile. Two LLM judges agree at QWK 0.90, but nobody has labeled by hand yet.
- **The real-reader numbers are tiny:** 3 readers, one day, and 18 posts under the current scorer.
  - The For You comparison rests on one reader. The quantum/AI reader's stored For You was Bluesky's cold-start
    onboarding page, so it is excluded.
  - The green-birds profile contradicts itself: feedback added "More: politics" and "all animals", so politics
    counts as relevant for that reader.
- **The For You comparison isn't apples to apples:** For You optimises engagement and has never seen the profile text.
  Frame it as "what you would otherwise scroll through".
- **Cost is the OpenRouter list price** for `google/gemini-decision-1`. Production calls Google directly, where the
  price may differ.
- **No engagement metrics:** there is 1 dismissal and 4 in-app interactions in total. Don't present engagement numbers.

## Issues found for the team

1. **The gate is strict.** It passes 61% (117/191) of the posts Opus rates relevant. On the 10-minute sample the
   ML-research reader got only 5 posts, and in production the green-birds reader passed 2 of 220. Digests can
   under-fill. Scanning the gate thresholds or adding a softer backfill tier would
   raise recall. In this run the flags, not the gate, were what kept junk out.
2. **Gemini rejects some posts.** It refused 87 of 92k scoring calls and 29 of 11.5k flag calls (sensitive content).
   In production a rejected post just isn't scored or flagged, and an unflagged post is still eligible.
3. **Production timing is lost on every deploy.** It lives only in the container logs and the latest
   `curation-timing:<uid>` KV entry, and autodeploy recreated the containers at 00:12 UTC. Persist one row per cycle
   if you want latency distributions.
4. **Candidate recall is not measured.** That covers the new rising-posts, followed-engagement and For You sources.
   Measuring it needs labels on what each source surfaces for a real profile.

## Files

- `prodq.py`: production questions and thresholds
- `full_score.py`: 92k-pair scoring and per-post flags
- `digest_sim.py`: digest simulation, Opus grading and report
- `ml_metrics.py`, `seeded.py`: ML metrics below
- `opus.py`: Opus labeler with images; shares the cache and ledger in `../feed-scoring/data`
- `results/sim_report.json`: digest simulation results

The real-reader comparison (claim 3) used a private production snapshot; its scripts and data are not published.

## ML metrics (production question wording; `ml_metrics.py`, `seeded.py`)

**Relevance, 896 Opus-graded pairs** (8 synthetic readers; the reference labels are Opus's):
- Ranking by the grade score: NDCG@10 **0.921** [0.87, 0.95], P@10 0.95, ROC-AUC **0.976** [0.967, 0.984], Spearman 0.87, worst reader NDCG 0.84.
- Grade agreement with Opus: QWK **0.83**. The ceiling is Sonnet vs Opus, at QWK 0.90.
- Inclusion gate (want ≥ 0.5 and grade ≥ 1.2): precision **0.90**, recall **0.66**, F1 0.76.
- `want` as a probability: AUC 0.971, Brier score 0.066, expected calibration error (ECE) 0.077.
- Digest value ranking (the production blend, gated): NDCG@10 0.888. Substance and opportunity cost about 0.03 NDCG, which isn't significant.

**Content flags, seeded set** (160 posts, labels known by construction):
- Per-flag ROC-AUC: clickbait 0.995, ragebait 1.000, misinformation 0.979, spam 0.976.
- Hidden or downranked: 76/80 bad posts and 4/80 benign look-alikes, so precision and recall are both 0.95 for "any bad". All 4 false positives are honest self-promotion.
- Per-flag precision is lower (spam 0.43) because the categories overlap. A clickbait post that is also promotion counts as a spam false positive.

**Content flags, real posts** (350 firehose posts, Opus labels; Gemini rejected 5):

| Flag | ROC-AUC | AP | P / R at production threshold |
|---|---|---|---|
| clickbait (11 positives) | 0.991 | 0.78 | 0.88 / 0.64 |
| ragebait (51) | 0.969 | 0.81 | 0.87 / 0.25 (hide threshold) |
| misinformation (6) | 0.976 | 0.75 | 0.75 / 0.50 |
| spam/promo (65) | 0.997 | 0.99 | 0.96 / 0.98 |
| low effort (90) | 0.865 | 0.71 | 0.92 / 0.13 |

The thresholds are tuned for precision: ragebait and low-effort posts are rarely hidden outright.
