# curator-curation

Run with `curator-curate run`, which loops every `CURATE_INTERVAL_S`. Use
`curator-curate once --user <did>` for a single cycle.

- `candidates.py`: cheap recall from follows, FTS search terms and popular posts.
- `llm/scorer.py`: batched Claude scoring with structured output and prompt caching.
- `llm/profile.py`: turns freeform feedback into structured preferences (the web package
  uses this too).
- `rank.py`: combines score, recency and engagement, applies a diversity penalty, and writes
  `feed_items`. Also builds the `__global__` fallback feed.
- `readers.py`: helpers for `users` and `preferences`.
- `evals/`: labelled posts and scorer agreement (to do).

Cost is the main decision; see DESIGN.md "Cost" before changing `SCORER_MODEL`.
