import json, numpy as np
from common import DATA
from evaluate import metrics, gold_load
from readers import READERS
gold = gold_load("opus")
def g(n):
    d = json.loads((DATA / f"gold_rel_{n}.json").read_text())
    return {r: {i: d.get(r, {}).get(i, {"grade": -1})["grade"] for i in gold[r]} for r in READERS}
def dec(n, k):
    d = json.loads((DATA / f"dec_rel_{n}.json").read_text())["scores"]
    return {r: {i: (d[r].get(i, {}).get(k) if d[r].get(i, {}).get(k) is not None else -1) for i in gold[r]} for r in READERS}
S = {"haiku_rubric": g("haiku_rubric_high"), "haiku_rubric_rep1": g("haiku_rubric_high_rep1"), "sonnet_judge": g("sonnet"), "jev_router_rubric": g("jev_rubric"),
     "gemini-decision-1:grade": dec("google_gemini-decision-1", "score"), "pplx-v1.3:grade": dec("perplexity_pplx-decider-v1.3_free", "score"),
     "d1-ultra:grade": dec("liquid_d1-ultra", "score"), "jev-1.14:grade": dec("typesafe_jev-1.14", "score")}
rng = np.random.default_rng(0)
for n, s in S.items():
    vals = [metrics({r: {i: v + rng.random() * 1e-6 for i, v in s[r].items()} for r in READERS}, gold)["ndcg@10"] for _ in range(50)]
    print(f"{n:28s} stable-order {metrics(s, gold)['ndcg@10']:.3f}  random tie-break mean {np.mean(vals):.3f} (sd {np.std(vals):.3f})")
# paired bootstrap with random tie-breaks, resampling readers' judged posts
keys = {r: list(gold[r]) for r in READERS}
diffs = {n: [] for n in S}
for b in range(300):
    idx = {r: rng.integers(0, len(keys[r]), len(keys[r])) for r in READERS}
    gg = {r: {f"{keys[r][j]}#{k}": gold[r][keys[r][j]] for k, j in enumerate(idx[r])} for r in READERS}
    m = {n: metrics({r: {f"{keys[r][j]}#{k}": s[r][keys[r][j]] + rng.random() * 1e-6 for k, j in enumerate(idx[r])} for r in READERS}, gg)["ndcg@10"] for n, s in S.items()}
    for n in S:
        diffs[n].append(m[n] - m["haiku_rubric"])
for n in S:
    print(f"{n:28s} Δ vs haiku_rubric 95% CI {np.percentile(diffs[n], 2.5):+.3f}..{np.percentile(diffs[n], 97.5):+.3f}")
