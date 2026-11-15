"""Approach B: local embedding similarity between reader profile and posts (no API cost)."""

import json
import re
import sys
import time

import numpy as np
from fastembed import TextEmbedding

from common import DATA
from readers import READERS

MODELS = {
    "bge-small": ("BAAI/bge-small-en-v1.5", "Represent this sentence for searching relevant passages: "),
    "bge-base": ("BAAI/bge-base-en-v1.5", "Represent this sentence for searching relevant passages: "),
    "mxbai-large": ("mixedbread-ai/mxbai-embed-large-v1", "Represent this sentence for searching relevant passages: "),
}


def sections(profile: str):
    pos, neg, cur = [], [], None
    for line in profile.splitlines():
        if line.startswith("# "):
            cur = neg if "Not" in line else pos
        elif line.startswith("- ") and cur is not None:
            cur.append(line[2:])
    return pos, neg


def main(names):
    corpus = [json.loads(l) for l in (DATA / "corpus.jsonl").read_text().splitlines()]
    texts = [p["text"] + (f"\n[link: {p['link']}]" if p.get("link") else "") for p in corpus]
    timing = {}
    for name in names:
        model_id, qprefix = MODELS[name]
        m = TextEmbedding(model_id, threads=16)
        t = time.monotonic()
        P = np.array(list(m.embed(texts, batch_size=64)))
        dt = time.monotonic() - t
        P /= np.linalg.norm(P, axis=1, keepdims=True)
        scores = {}
        for rid, prof in READERS.items():
            pos, neg = sections(prof)
            whole = qprefix + re.sub(r"# Not interested.*", "", prof, flags=re.S)
            Q = np.array(list(m.embed([whole] + [qprefix + s for s in pos] + [qprefix + s for s in neg])))
            Q /= np.linalg.norm(Q, axis=1, keepdims=True)
            sim = P @ Q.T
            s_whole = sim[:, 0]
            s_pos = sim[:, 1 : 1 + len(pos)].max(1)
            s_neg = sim[:, 1 + len(pos) :].max(1) if neg else np.zeros(len(P))
            scores[rid] = {
                "whole": s_whole.tolist(),
                "maxpos": s_pos.tolist(),
                "maxpos_minus_neg": (s_pos - 0.5 * np.maximum(s_neg - s_pos, 0) - 0.3 * s_neg).tolist(),
            }
        (DATA / f"emb_{name}.json").write_text(json.dumps({"ids": [p["id"] for p in corpus], "scores": scores}))
        np.save(DATA / f"emb_{name}.npy", P.astype(np.float32))
        timing[name] = {"posts": len(texts), "seconds": dt, "ms_per_post": 1000 * dt / len(texts), "dim": P.shape[1]}
        print(name, timing[name], flush=True)
    path = DATA / "emb_timing.json"
    old = json.loads(path.read_text()) if path.exists() else {}
    path.write_text(json.dumps(old | timing, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:] or list(MODELS))
