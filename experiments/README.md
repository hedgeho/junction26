# Experiments

Offline evaluations behind Agora's scoring and filtering choices and the numbers in the pitch. They are research scripts, kept as they were run, and are not part of the application.

- [`feed-scoring/`](feed-scoring/REPORT.md) (2026-11-14) compares ways to score posts for a reader and to detect clickbait, rage bait, misinformation and spam: Claude prompts, embeddings, tag-based cascades, OpenRouter routers and decision models. It led to Gemini Decision 1 on the Decisions API.
- [`pitch-metrics/`](pitch-metrics/REPORT.md) (2026-11-15) runs the production questions and digest rules over 11.5k real Bluesky posts and 8 synthetic readers. Claude Opus 5.5 grades the results blind.

Headline results:

- 92% of digest posts are relevant to the reader, against 0.6% of the raw firehose. None are junk, against 15% of the firehose.
- On 350 real posts, the content flags agree with Opus on about 90% of the posts they hide (clickbait 88%, rage bait 87%, spam 96%).
- Ranking reaches NDCG@10 0.92 against Opus's grades. Sonnet 5.5 reading every post reaches 0.94, at about ten times the cost.
- Scoring one post for one reader costs about $0.00006 and takes 0.29 s.

The judge is a model, not people, and 5 of the 8 readers are synthetic. Each report lists its caveats.

## What is not included

Collected posts, model response caches, cost ledgers and reference labels are not published, and neither is the real-reader comparison, which used a private production snapshot. Only aggregate results are in each `results/` directory. To re-run, collect a fresh sample with `feed-scoring/collect.py`; labels and scores will differ from the reports.

## Running

```sh
cd experiments/feed-scoring
export ANTHROPIC_API_KEY=... OPENROUTER_API_KEY=...   # or put them in experiments/.env
uv run python collect.py
```

Scripts write to `data/`, which is ignored by Git. `common.py` caches every model call and stops at a fixed spend budget. `pitch-metrics/` imports from `feed-scoring/`; run it with the same environment, for example `uv run --project ../feed-scoring python digest_sim.py`.
