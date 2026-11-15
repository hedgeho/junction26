"""usage: build_pool.py corpus N   |   build_pool.py pool K RANDOM system1 system2 ...

Pooled judging (TREC-style): each reader's judged set is the union of every system's top-K plus random posts,
so no single system's blind spots define the reference set."""

import json
import random
import sys

from common import DATA, posts
from readers import READERS
from systems import load

if sys.argv[1] == "corpus":
    rnd = random.Random(0)
    seen, ps = set(), []
    for p in posts():
        k = p["text"].lower()[:120]
        if k not in seen:
            seen.add(k)
            ps.append(p)
    rnd.shuffle(ps)
    ps = ps[: int(sys.argv[2])]
    (DATA / "corpus.jsonl").write_text("".join(json.dumps(p) + "\n" for p in ps))
    print(len(ps), "posts in corpus")
else:
    k, nrand, names = int(sys.argv[2]), int(sys.argv[3]), sys.argv[4:]
    systems = load(llm=[n.split(":", 1)[1] for n in names if n.startswith("llm:")],
                   topics=[n.split(":")[1] for n in names if n.startswith("topic:")])
    ids = [json.loads(l)["id"] for l in (DATA / "corpus.jsonl").read_text().splitlines()]
    rnd = random.Random(1)
    pool = {}
    for r in READERS:
        chosen = []
        for n in names:
            s = systems[n][r]
            chosen += sorted(s, key=s.get, reverse=True)[:k]
        chosen += rnd.sample(ids, nrand)
        pool[r] = list(dict.fromkeys(chosen))
    (DATA / "pool.json").write_text(json.dumps(pool))
    print({r: len(v) for r, v in pool.items()}, sum(map(len, pool.values())))
