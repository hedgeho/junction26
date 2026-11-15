"""Standard ML metrics for the production scorer and triage (production question wording)."""
import asyncio, json
import numpy as np
from sklearn.metrics import roc_auc_score, average_precision_score, cohen_kappa_score
import prodq
from common import DATA
from evaluate import metrics, bootstrap
from readers import READERS

scores = json.loads(open("data/full_scores.json").read())
gold = json.loads((DATA / "gold_rel_opus.json").read_text())
sonnet = json.loads((DATA / "gold_rel_sonnet.json").read_text())
ok = lambda s: s and "error" not in s

# Relevance on the 896 reference pairs
sys_ = {"prod_grade": {r: {i: (scores[r][i]["rel"] if ok(scores[r].get(i)) else -1) for i in gold[r]} for r in READERS},
        "prod_value": {r: {i: ((0.65*s["rel"]+0.25*s["sub"]+0.1*s["opp"]) * (s["want"] >= .5 and s["rel"] >= .4) if ok(s := scores[r].get(i)) else -1)
                           for i in gold[r]} for r in READERS}}
for n, s in sys_.items():
    m = metrics(s, gold)
    print(n, {k: round(v, 3) for k, v in m.items() if not isinstance(v, dict)})
ci = bootstrap(sys_, gold, "prod_grade", n=300)
print("bootstrap CIs:", {n: {k: [round(x, 3) for x in v] for k, v in c.items()} for n, c in ci.items()})
# Grade agreement and gate precision/recall
y, yhat, sg = [], [], []
for r in READERS:
    for i, g in gold[r].items():
        s = scores[r].get(i)
        if ok(s):
            y.append(g["grade"]); yhat.append(int(round(s["rel"] * 3)))
            if i in sonnet.get(r, {}):
                sg.append((g["grade"], sonnet[r][i]["grade"]))
print(f"QWK prod vs Opus: {cohen_kappa_score(y, yhat, weights='quadratic'):.3f} (n={len(y)})")
print(f"QWK Sonnet vs Opus (judge ceiling): {cohen_kappa_score(*zip(*sg), weights='quadratic'):.3f}")
gate = [(gold[r][i]["grade"] >= 2, s["want"] >= .5 and s["rel"] >= .4) for r in READERS for i in gold[r] if ok(s := scores[r].get(i))]
tp = sum(a and b for a, b in gate); fp = sum(b and not a for a, b in gate); fn = sum(a and not b for a, b in gate)
print(f"gate precision={tp/(tp+fp):.2f} recall={tp/(tp+fn):.2f} F1={2*tp/(2*tp+fp+fn):.2f}")
# Calibration of want vs Opus relevant
w = np.array([scores[r][i]["want"] for r in READERS for i in gold[r] if ok(scores[r].get(i))])
t = np.array([gold[r][i]["grade"] >= 2 for r in READERS for i in gold[r] if ok(scores[r].get(i))])
bins = np.linspace(0, 1, 11); idx = np.clip(np.digitize(w, bins) - 1, 0, 9)
ece = sum(abs(w[idx == b].mean() - t[idx == b].mean()) * (idx == b).sum() for b in range(10) if (idx == b).any()) / len(w)
print(f"want: AUC={roc_auc_score(t, w):.3f} Brier={np.mean((w - t) ** 2):.3f} ECE={ece:.3f}")

async def flags_eval():
    FL = list(prodq.FLAGS)
    for src, gfile in (("synth", None), ("qsample", "gold_q_qsample_opus.json")):
        items = [json.loads(l) for l in (DATA / f"{src}.jsonl").read_text().splitlines()]
        async def safe(p):
            try:
                return await prodq.flags(p, f"pitch:ml:{src}")
            except RuntimeError:
                return None, None
        res = await asyncio.gather(*(safe(p) for p in items))
        print("rejected:", sum(f is None for f, _ in res))
        g = json.loads((DATA / gfile).read_text()) if gfile else None
        print(f"\n{src}: " + ("labels by construction" if not g else "Opus labels"))
        for f in FL:
            yt, ys = [], []
            for p, (fl, _) in zip(items, res):
                if fl is None: continue
                if g is None:
                    lab = p["label"] if p["label"] != "spam_promo" else "spam_promo"
                    yt.append(lab == f or (f == "spam_promo" and lab == "spam"))
                else:
                    if p["id"] not in g: continue
                    yt.append(g[p["id"]][f])
                ys.append(fl[f])
            if sum(yt) and sum(yt) < len(yt):
                thr = prodq.HIDE_AT.get(f, prodq.DOWNRANK_AT.get(f))
                pred = [s >= thr for s in ys]
                tp = sum(a and b for a, b in zip(yt, pred)); pp = sum(pred)
                print(f"  {f:15s} pos={sum(yt):3d} AUC={roc_auc_score(yt, ys):.3f} AP={average_precision_score(yt, ys):.3f} "
                      f"@prod-thr {thr}: P={tp/pp if pp else float('nan'):.2f} R={tp/sum(yt):.2f}")
asyncio.run(flags_eval())
