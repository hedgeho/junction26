"""Ranking quality of every system against the pooled Opus reference labels."""

import json
import sys

import numpy as np
from scipy.stats import spearmanr
from sklearn.metrics import roc_auc_score

from common import DATA
from readers import READERS


def ndcg(order, gold, k=10):
    gains = [2 ** gold[i] - 1 for i in order[:k]]
    ideal = sorted((2 ** g - 1 for g in gold.values()), reverse=True)[:k]
    dcg = sum(g / np.log2(j + 2) for j, g in enumerate(gains))
    idcg = sum(g / np.log2(j + 2) for j, g in enumerate(ideal))
    return dcg / idcg if idcg else np.nan


TIE_DRAWS = 20


def metrics(scores: dict, gold: dict) -> dict:
    """scores/gold: {reader: {post_id: value}}. Macro-average over readers.
    Ties in the score are broken uniformly at random (ndcg/p@10 averaged over TIE_DRAWS draws), so a
    system gets no credit from the input order of tied posts."""
    rng = np.random.default_rng(12345)
    rows = []
    for r in READERS:
        g = {i: v["grade"] if isinstance(v, dict) else v for i, v in gold[r].items()}
        ids = [i for i in g if i in scores[r]]
        s = np.array([scores[r][i] for i in ids], dtype=float)
        y = np.array([g[i] for i in ids])
        gg = {i: g[i] for i in ids}
        draws = TIE_DRAWS if len(set(s.tolist())) < len(s) else 1
        nd, p2, p3 = [], [], []
        for _ in range(draws):
            order = [ids[j] for j in np.lexsort((rng.random(len(s)), -s))]
            nd.append(ndcg(order, gg))
            p2.append(np.mean([gg[i] >= 2 for i in order[:10]]))
            p3.append(np.mean([gg[i] == 3 for i in order[:10]]))
        rows.append({
            "ndcg@10": float(np.mean(nd)),
            "p@10(>=2)": float(np.mean(p2)),
            "p@10(=3)": float(np.mean(p3)),
            "auc(>=2)": roc_auc_score(y >= 2, s) if 0 < (y >= 2).sum() < len(y) else np.nan,
            "spearman": spearmanr(s, y).statistic,
        })
    out = {k: float(np.nanmean([r[k] for r in rows])) for k in rows[0]}
    out["ndcg@10_min"] = float(np.nanmin([r["ndcg@10"] for r in rows]))
    return out


def gold_load(name="opus"):
    return json.loads((DATA / f"gold_rel_{name}.json").read_text())


if __name__ == "__main__":
    from systems import load

    gold = gold_load(sys.argv[1] if len(sys.argv) > 1 else "opus")
    systems = load(llm=[l for l in sys.argv[2].split(",") if l] if len(sys.argv) > 2 else (),
                   topics=sys.argv[3].split(",") if len(sys.argv) > 3 else ())
    res = {n: metrics(s, gold) for n, s in systems.items()}
    for n, m in sorted(res.items(), key=lambda x: -x[1]["ndcg@10"]):
        print(f"{n:45s} " + " ".join(f"{k}={v:.3f}" for k, v in m.items()))


def bootstrap(systems: dict, gold: dict, ref: str, n=300, seed=0):
    """Paired bootstrap over judged posts (resampled within each reader). Returns 95% CIs for
    ndcg@10 and auc(>=2), and for the difference vs `ref`."""
    rng = np.random.default_rng(seed)
    keys = {r: list(gold[r]) for r in READERS}
    draws = {s: {"ndcg@10": [], "auc(>=2)": []} for s in systems}
    for b in range(n):
        g, idx = {}, {}
        for r in READERS:
            pick = rng.integers(0, len(keys[r]), len(keys[r]))
            # suffix duplicates so each draw is a distinct item
            idx[r] = [(keys[r][j], f"{keys[r][j]}#{k}") for k, j in enumerate(pick)]
            g[r] = {new: gold[r][old] for old, new in idx[r]}
        for s, sc in systems.items():
            m = metrics({r: {new: sc[r].get(old, -2) for old, new in idx[r]} for r in READERS}, g)
            for k in draws[s]:
                draws[s][k].append(m[k])
    out = {}
    for s in systems:
        out[s] = {}
        for k in ("ndcg@10", "auc(>=2)"):
            a, d = np.array(draws[s][k]), np.array(draws[s][k]) - np.array(draws[ref][k])
            out[s][k] = (float(np.percentile(a, 2.5)), float(np.percentile(a, 97.5)))
            out[s][k + " diff_vs_ref"] = (float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5)))
    return out
