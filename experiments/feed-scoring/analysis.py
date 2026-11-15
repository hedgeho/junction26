"""Final ranking analysis: metrics + paired-bootstrap CIs, ceilings, prefilter recall, cascades."""
import json
import numpy as np
from common import DATA
from readers import READERS
from systems import load
from evaluate import metrics, gold_load, bootstrap

gold = gold_load("opus")
LLM = ["haiku_b20_low", "haiku_b20_medium", "haiku_b20_low_rep1", "haiku_b20_low_rep2", "haiku_b20_medium_rep1", "haiku_b20_medium_rep2", "sonnet_b20_low", "opus_b20_low"]
S = load(emb=("bge-small",), llm=LLM, topics=["haiku_b25_low"])
sj = json.loads((DATA / "gold_rel_sonnet.json").read_text())
S["judge:sonnet_high"] = {r: {i: v["grade"] for i, v in sj[r].items()} for r in READERS}
N = len(json.loads(open(DATA / "corpus.jsonl").readline()) and open(DATA / "corpus.jsonl").readlines())
print("corpus", N, "judged pairs", sum(map(len, gold.values())))
print("positives per reader (>=2, =3):", {r: (sum(v["grade"] >= 2 for v in gold[r].values()), sum(v["grade"] == 3 for v in gold[r].values())) for r in READERS})
oracle = {r: {i: v["grade"] for i, v in gold[r].items()} for r in READERS}
print("oracle p@10(>=2) ceiling:", round(metrics(oracle, gold)["p@10(>=2)"], 3))

def cascade(pre, final, k):
    out = {}
    for r in READERS:
        top = set(sorted(S[pre][r], key=S[pre][r].get, reverse=True)[:k])
        out[r] = {i: (S[final][r][i] if i in top and i in S[final][r] else -2) for i in gold[r]}
    return out

for pre in ["emb:bge-small:whole", "topic:haiku_b25_low:subst+flags", "llm:haiku_b20_low"]:
    for k in (200, 500):
        S[f"cascade:{pre.split(':')[0]}{'' if pre.startswith('llm') else ''}-top{k}->sonnet"] = cascade(pre, "llm:sonnet_b20_low", k)

def recall(pre, k):
    rs = []
    for r in READERS:
        top = set(sorted(S[pre][r], key=S[pre][r].get, reverse=True)[:k])
        pos = [i for i, v in gold[r].items() if v["grade"] >= 2]
        if pos: rs.append(np.mean([i in top for i in pos]))
    return round(float(np.mean(rs)), 3)

print("\nprefilter recall of grade>=2 posts, top-K of", N)
for pre in ["emb:bge-small:whole", "emb:bge-small:maxpos", "topic:haiku_b25_low:plain", "topic:haiku_b25_low:subst+flags", "llm:haiku_b20_low"]:
    print(f"  {pre:35s}", {k: recall(pre, k) for k in (100, 200, 500, 1000, 2000)})

res = {n: metrics(s, gold) for n, s in S.items()}
ci = bootstrap(S, gold, ref="llm:haiku_b20_low", n=200)
print(f"\n{'system':45s} {'ndcg@10':>8s} {'95%CI':>13s} {'Δ vs haiku 95%CI':>18s} {'p@10':>6s} {'AUC':>6s} {'95%CI':>13s} {'spear':>6s} {'ndcg_min':>8s}")
for n, m in sorted(res.items(), key=lambda x: -x[1]["ndcg@10"]):
    c = ci[n]
    print(f"{n:45s} {m['ndcg@10']:8.3f} {c['ndcg@10'][0]:6.3f}-{c['ndcg@10'][1]:.3f} {c['ndcg@10 diff_vs_ref'][0]:+8.3f}..{c['ndcg@10 diff_vs_ref'][1]:+.3f} "
          f"{m['p@10(>=2)']:6.3f} {m['auc(>=2)']:6.3f} {c['auc(>=2)'][0]:6.3f}-{c['auc(>=2)'][1]:.3f} {m['spearman']:6.3f} {m['ndcg@10_min']:8.3f}")
json.dump({"metrics": res, "ci": ci}, open(DATA / "analysis.json", "w"), indent=1, default=float)
