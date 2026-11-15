"""Bad-content detection: known-label synthetic set + Opus-labelled real sample."""

import json
import re
import sys

import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score

from common import DATA
from tag import FLAGS

BAD = ["clickbait", "ragebait", "misinformation", "spam_promo"]


def heuristic(p):
    t = p["text"] + " " + (p.get("link") or "")
    low = t.lower()
    caps = sum(c.isupper() for c in t) / max(1, sum(c.isalpha() for c in t))
    return {
        "clickbait": float(bool(re.search(r"you won'?t believe|the real reason|this is why|what happens next|"
                                          r"will (shock|surprise)|number \d|reveals?|secret|nobody (is )?talk", low))),
        "ragebait": float(caps > 0.3 or "!!" in t or bool(re.search(r"\b(idiots?|morons?|disgusting|pathetic)\b", low))),
        "misinformation": float(bool(re.search(r"they don'?t want you|wake up|big pharma|mainstream media won'?t|"
                                               r"do your own research|cover[- ]?up", low))),
        "spam_promo": float(len(re.findall(r"#\w+", t)) >= 4 or bool(re.search(
            r"follow (for|4) follow|giveaway|\bfree\b|dm me|link in bio|airdrop|retweet|repost to win", low))),
    }


def synth_report(taggers):
    items = [json.loads(l) for l in (DATA / "synth.jsonl").read_text().splitlines()]
    preds = {"heuristic": {p["id"]: heuristic(p) for p in items}}
    for name in taggers:
        path = DATA / f"tags_synth_{name}.json"
        if path.exists():
            preds[f"tagger:{name}"] = json.loads(path.read_text())["tags"]
    for name in ("opus", "sonnet"):
        path = DATA / f"gold_q_synth_{name}.json"
        if path.exists():
            preds[f"judge:{name}"] = {i: {f: float(v[f]) for f in FLAGS} for i, v in json.loads(path.read_text()).items()}
    print("\n== Synthetic seeded set (n=%d, 4 bad kinds x20 + 4 benign look-alike kinds x20) ==" % len(items))
    print(f"{'system':28s} " + " ".join(f"{f[:10]:>22s}" for f in BAD) + "   any-bad P/R")
    out = {}
    for name, pr in preds.items():
        cells, row = [], {}
        for f in BAD:
            y = np.array([p["label"] == f for p in items])
            s = np.array([pr[p["id"]][f] for p in items])
            yhat = s >= 0.5
            prec = (yhat & y).sum() / max(1, yhat.sum())
            rec = (yhat & y).sum() / y.sum()
            auc = roc_auc_score(y, s) if len(set(s)) > 1 else 0.5
            row[f] = {"auc": auc, "precision": prec, "recall": rec}
            cells.append(f"auc{auc:.2f} P{prec:.2f} R{rec:.2f}")
        ybad = np.array([not p["label"].startswith("benign") for p in items])
        sbad = np.array([max(pr[p["id"]][f] for f in BAD) >= 0.5 for p in items])
        row["any_bad"] = {"precision": (sbad & ybad).sum() / max(1, sbad.sum()), "recall": (sbad & ybad).sum() / ybad.sum(),
                          "false_pos_on_benign": (sbad & ~ybad).sum() / (~ybad).sum()}
        out[name] = row
        print(f"{name:28s} " + " ".join(f"{c:>22s}" for c in cells) +
              f"   {row['any_bad']['precision']:.2f}/{row['any_bad']['recall']:.2f} (FP on benign {row['any_bad']['false_pos_on_benign']:.2f})")
    # Which benign look-alikes get flagged
    for name in preds:
        if name.startswith("tagger") or name.startswith("judge"):
            fp = {}
            for p in items:
                if p["label"].startswith("benign") and max(preds[name][p["id"]][f] for f in BAD) >= 0.5:
                    fp[p["label"]] = fp.get(p["label"], 0) + 1
            print(f"  false positives by benign kind, {name}: {fp}")
    return out


def real_report(tagger):
    gold = json.loads((DATA / "gold_q_qsample_opus.json").read_text())
    sample = [json.loads(l) for l in (DATA / "qsample.jsonl").read_text().splitlines()]
    path = DATA / f"tags_corpus_{tagger}.json"
    tags = json.loads((path if path.exists() else DATA / f"tags_qsample_{tagger}.json").read_text())["tags"]
    rand = [p for p in sample if p["qs"] == "random"]
    print(f"\n== Real Jetstream posts: Opus labels on {len(sample)} posts ({len(rand)} random + enriched by tagger) ==")
    print("prevalence in random sample (Opus):",
          {f: f"{np.mean([gold[p['id']][f] for p in rand if p['id'] in gold]):.3f}" for f in FLAGS})
    res = {}
    for f in FLAGS:
        y = np.array([gold[p["id"]][f] for p in sample if p["id"] in gold and p["id"] in tags])
        s = np.array([tags[p["id"]][f] for p in sample if p["id"] in gold and p["id"] in tags])
        yhat = s >= 0.5
        res[f] = {"n_pos": int(y.sum()), "auc": roc_auc_score(y, s) if 0 < y.sum() < len(y) else float("nan"),
                  "ap": average_precision_score(y, s) if y.sum() else float("nan"),
                  "precision@0.5": (yhat & y).sum() / max(1, yhat.sum()), "recall@0.5": (yhat & y).sum() / max(1, y.sum())}
        h = np.array([heuristic(p).get(f, 0) for p in sample if p["id"] in gold and p["id"] in tags])
        res[f]["heuristic_auc"] = roc_auc_score(y, h) if f in BAD and 0 < y.sum() < len(y) and len(set(h)) > 1 else float("nan")
        print(f"  {f:15s} " + " ".join(f"{k}={v:.3f}" if isinstance(v, float) else f"{k}={v}" for k, v in res[f].items()))
    return res


if __name__ == "__main__":
    out = {"synth": synth_report(sys.argv[1].split(","))}
    if (DATA / "gold_q_qsample_opus.json").exists():
        out["real"] = real_report(sys.argv[2])
    (DATA / "quality_results.json").write_text(json.dumps(out, indent=1, default=float))
