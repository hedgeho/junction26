"""Decision models (Decisions API) vs the LLM baselines, on relevance and bad content."""
import glob
import json

import numpy as np
from sklearn.metrics import average_precision_score, cohen_kappa_score, roc_auc_score

from common import DATA
from evaluate import bootstrap, gold_load, metrics
from readers import READERS
from systems import zrank
from tag import FLAGS

gold = gold_load("opus")
opus = {r: {i: v["grade"] for i, v in gold[r].items()} for r in READERS}
BAD = ["clickbait", "ragebait", "misinformation", "spam_promo"]


def short(path, pre):
    return path.split(f"dec_{pre}")[1].removesuffix(".json")


def fill(d):
    return {r: {i: (d[r].get(i) if d[r].get(i) is not None else -1) for i in gold[r]} for r in READERS}


# ---------- relevance ----------
S, info = {}, {}
for n in ["haiku_rubric_high", "sonnet", "jev_rubric"]:
    d = json.loads((DATA / f"gold_rel_{n}.json").read_text())
    S[f"llm:{n}"] = fill({r: {i: v["grade"] for i, v in d.get(r, {}).items()} for r in READERS})
d = json.loads((DATA / "score_sonnet_b20_low.json").read_text())["scores"]
S["llm:sonnet_b20_low"] = fill({r: {i: v["relevance"] for i, v in d[r].items()} for r in READERS})
for f in sorted(glob.glob(str(DATA / "dec_rel_*.json"))):
    name = short(f, "rel_")
    d = json.loads(open(f).read())
    sc, st = d["scores"], d["stats"]
    info[name] = st
    grade = fill({r: {i: v["score"] for i, v in sc[r].items()} for r in READERS})
    S[f"{name}:grade"] = grade
    if any(v["want"] is not None for r in sc for v in sc[r].values()):
        want = fill({r: {i: v["want"] for i, v in sc[r].items()} for r in READERS})
        S[f"{name}:want"] = want
        S[f"{name}:grade+want"] = {r: {i: (zrank(grade[r])[i] + zrank(want[r])[i]) / 2 for i in gold[r]} for r in READERS}
    am = [(opus[r][i], v["argmax"]) for r in READERS for i, v in sc[r].items() if v.get("argmax") is not None and i in opus[r]]
    info[name]["qwk"] = cohen_kappa_score(*zip(*am), weights="quadratic") if am else float("nan")

res = {n: metrics(s, gold) for n, s in S.items()}
ci = bootstrap(S, gold, ref="llm:haiku_rubric_high", n=200)
print(f"{'system':58s} {'ndcg@10':>7s} {'Δ vs haiku-rubric 95%CI':>23s} {'p@10':>5s} {'AUC':>5s} {'QWK':>5s} {'$/1k':>8s} {'p50':>5s} {'p90':>5s} err")
rel_rows = []
for n, m in sorted(res.items(), key=lambda x: -x[1]["ndcg@10"]):
    base = n.rsplit(":", 1)[0]
    st = info.get(base, {})
    pairs = sum(map(len, gold.values()))
    c = ci[n]["ndcg@10 diff_vs_ref"]
    row = {"system": n, **m, "diff_ci": c, "qwk": st.get("qwk"), "cost_per_1k": 1000 * st["cost"] / pairs if st else None,
           "p50": st.get("latency_p50"), "p90": st.get("latency_p90"), "errors": st.get("errors")}
    rel_rows.append(row)
    print(f"{n:58s} {m['ndcg@10']:7.3f} {c[0]:+10.3f}..{c[1]:+.3f} {m['p@10(>=2)']:5.2f} {m['auc(>=2)']:5.3f} "
          f"{st.get('qwk', float('nan')):5.2f} {row['cost_per_1k'] or float('nan'):8.4f} {st.get('latency_p50', float('nan')):5.2f} "
          f"{st.get('latency_p90', float('nan')):5.2f} {st.get('errors', '')}")

# ---------- bad content ----------
synth = [json.loads(l) for l in (DATA / "synth.jsonl").read_text().splitlines()]
qs = [json.loads(l) for l in (DATA / "qsample.jsonl").read_text().splitlines()]
qgold = json.loads((DATA / "gold_q_qsample_opus.json").read_text())


def auc(y, s):
    y, s = np.array(y), np.array(s, dtype=float)
    return roc_auc_score(y, s) if 0 < y.sum() < len(y) and len(set(s)) > 1 else float("nan")


def load_tags(kind, name):
    if kind == "dec":
        return json.loads((DATA / f"dec_q_{name}.json").read_text())
    return {"tags": json.loads((DATA / name).read_text())["tags"], "stats": {}}


qual = {}
names = sorted({short(f, "q_synth_") for f in glob.glob(str(DATA / "dec_q_synth_*.json"))})
base = [("haiku tagger", "tags_synth_haiku_b20_low.json", "tags_corpus_haiku_b25_low.json"),
        ("sonnet tagger", "tags_synth_sonnet_b20_low.json", None),
        ("jev-router tagger", "tags_synth_jev_b20_none.json", "tags_qsample_jev_b25_none.json")]
rows = [(b, load_tags("llm", s), load_tags("llm", q) if q else None) for b, s, q in base]
for n in names:
    q = DATA / f"dec_q_qsample_{n}.json"
    rows.append((n, load_tags("dec", f"synth_{n}"), json.loads(q.read_text()) if q.exists() else None))

print(f"\n{'tagger':42s} | synth AUC: {'click':>5s} {'rage':>5s} {'misin':>5s} {'spam':>5s} {'anyBad':>6s} R@P.9 | real AUC: "
      f"{'click':>5s} {'rage':>5s} {'misin':>5s} {'spam':>5s} {'loweff':>6s} {'mean4':>5s} | $/1k p50")
for n, sy, rq in rows:
    t = sy["tags"]
    ok = [p for p in synth if p["id"] in t and all(t[p["id"]].get(f) is not None for f in BAD)]
    sa = {f: auc([p["label"] == f for p in ok], [t[p["id"]][f] for p in ok]) for f in BAD}
    yb = np.array([not p["label"].startswith("benign") for p in ok])
    sb = np.array([max(t[p["id"]][f] for f in BAD) for p in ok])
    sa["any_bad"] = auc(yb, sb)
    # recall at the loosest threshold that keeps precision >= 0.9
    best = 0.0
    for th in sorted(set(sb)):
        pr = (sb >= th)
        if pr.sum() and (pr & yb).sum() / pr.sum() >= 0.9:
            best = max(best, (pr & yb).sum() / yb.sum())
    sa["recall@p0.9"] = best
    ra, cost, p50 = {}, float("nan"), float("nan")
    if rq:
        tt = rq["tags"]
        ids = [p["id"] for p in qs if p["id"] in qgold and p["id"] in tt and all(tt[p["id"]].get(f) is not None for f in FLAGS)]
        ra = {f: auc([qgold[i][f] for i in ids], [tt[i][f] for i in ids]) for f in FLAGS}
        ra["ap"] = {f: average_precision_score([qgold[i][f] for i in ids], [tt[i][f] for i in ids]) for f in FLAGS}
        st = rq.get("stats") or {}
        if st.get("items"):
            cost, p50 = 1000 * st["cost"] / st["items"], st["latency_p50"]
    qual[n] = {"synth": sa, "real": ra, "cost_per_1k_posts": cost, "p50": p50,
               "errors": (sy.get("stats") or {}).get("errors", 0) + ((rq or {}).get("stats") or {}).get("errors", 0)}
    g = lambda d, k: d.get(k, float("nan"))
    m4 = np.nanmean([g(ra, f) for f in BAD]) if ra else float("nan")
    print(f"{n:42s} | {'':10s} " + " ".join(f"{sa[f]:5.3f}" for f in BAD) + f" {sa['any_bad']:6.3f} {sa['recall@p0.9']:5.2f} | {'':9s} "
          + " ".join(f"{g(ra, f):5.3f}" for f in BAD) + f" {g(ra, 'low_effort'):6.3f} {m4:5.3f} | {cost:.4f} {p50:.2f}")

json.dump({"relevance": rel_rows, "quality": qual, "stats": info}, open(DATA / "dec_results.json", "w"), indent=1, default=float)
