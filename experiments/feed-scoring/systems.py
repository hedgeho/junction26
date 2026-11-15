"""Load every approach's scores as {system: {reader: {post_id: score}}} over the corpus (or pool)."""

import json

import numpy as np

from common import DATA
from readers import READERS
from tag import FLAGS


def corpus():
    return [json.loads(l) for l in (DATA / "corpus.jsonl").read_text().splitlines()]


def _emb(name):
    d = json.loads((DATA / f"emb_{name}.json").read_text())
    return {f"emb:{name}:{variant}": {r: dict(zip(d["ids"], d["scores"][r][variant])) for r in READERS}
            for variant in ("whole", "maxpos", "maxpos_minus_neg")}


def _llm(name):
    d = json.loads((DATA / f"score_{name}.json").read_text())["scores"]
    return {f"llm:{name}": {r: {i: v["relevance"] for i, v in d[r].items()} for r in d}}


def topic_scores(tags, weights, substance=True, penalize=True):
    out = {}
    for r in READERS:
        w = weights[r]
        s = {}
        for pid, t in tags.items():
            m = sum(w.get(tp, 0) * pw for tp, pw in zip(t["topics"], (1.0, 0.6, 0.4)))
            if substance:
                m = m * (0.3 + 0.7 * t["substance"]) if m > 0 else m
            if penalize:
                m -= 0.5 * max(t[f] for f in FLAGS if f != "low_effort")
            s[pid] = m
        out[r] = s
    return out


def _topics(tagname, readername="sonnet"):
    tags = json.loads((DATA / f"tags_corpus_{tagname}.json").read_text())["tags"]
    w = json.loads((DATA / f"reader_topics_{readername}.json").read_text())
    return {
        f"topic:{tagname}:plain": topic_scores(tags, w, substance=False, penalize=False),
        f"topic:{tagname}:subst+flags": topic_scores(tags, w),
    }


def load(emb=("bge-small", "bge-base", "mxbai-large"), llm=(), topics=()):
    systems = {}
    for e in emb:
        if (DATA / f"emb_{e}.json").exists():
            systems |= _emb(e)
    for n in llm:
        if (DATA / f"score_{n}.json").exists():
            systems |= _llm(n)
    for t in topics:
        if (DATA / f"tags_corpus_{t}.json").exists():
            systems |= _topics(t)
    return systems


def zrank(d: dict) -> dict:
    """Map scores to percentile ranks in [0,1] (for fusing heterogeneous systems)."""
    ids = list(d)
    v = np.array([d[i] for i in ids], dtype=float)
    ranks = v.argsort().argsort() / max(len(v) - 1, 1)
    return dict(zip(ids, ranks))
