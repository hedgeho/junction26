"""Simulate the production digest (digests.build at c573ea1) on the 11.5k-post firehose sample for 8 synthetic readers,
then grade every delivered post with Opus (blind, shuffled) and compare with baselines.

usage: digest_sim.py label | report
"""
import asyncio, json, random, sys
from pathlib import Path
import prodq
import opus
from common import DATA as FS
from gold import REL_SYSTEM, REL_SCHEMA, Q_SYSTEM, Q_SCHEMA
from readers import READERS
from systems import _emb, _llm

HERE = Path(__file__).parent / "data"
LIMITS = (10, 20)
corpus = {p["id"]: p for p in map(json.loads, (FS / "corpus.jsonl").read_text().splitlines())}
scores = json.loads((HERE / "full_scores.json").read_text())
flags = {k: (v if v and "error" not in v else None) for k, v in json.loads((HERE / "flags.json").read_text()).items()}
gold = json.loads((FS / "gold_rel_opus.json").read_text())


def ok(s):
    return s and "error" not in s


def digest(r, limit, use_flags=True, gate=True):
    sc = scores[r]
    fl = flags if use_flags else {}
    def value(i):
        s = sc[i]
        return (0.65 * s["rel"] + 0.25 * s["sub"] + 0.1 * s["opp"]) * prodq.penalty(fl.get(i))
    inc = [i for i, s in sc.items() if ok(s) and (not gate or (s["want"] >= 0.5 and s["rel"] >= 0.4)) and not prodq.hidden(fl.get(i))]
    near = [i for i, s in sc.items() if ok(s) and gate and s["want"] < 0.5 and s["rel"] >= 0.5 and s["sub"] >= 0.75
            and fl.get(i) and not prodq.hidden(fl[i]) and prodq.penalty(fl[i]) == 1]
    pool = sorted(inc, key=lambda i: (-value(i), i))
    ser = sorted(near, key=lambda i: (-value(i), i))
    selected, authors, texts, stories = [], {}, set(), set()
    def take(i):
        p = corpus[i]
        t, st = " ".join(p["text"].casefold().split()), p.get("link")
        if i in selected or t in texts or (st and st in stories) or authors.get(p["author"], 0) >= 2:
            return
        selected.append(i); texts.add(t); authors[p["author"]] = authors.get(p["author"], 0) + 1
        if st:
            stories.add(st)
    reserved = min(len(ser), int(limit * 0.1))
    for i in pool:
        if len(selected) >= limit - reserved:
            break
        take(i)
    for i in ser:
        if len(selected) >= limit:
            break
        take(i)
    rank = {i: n for n, i in enumerate(pool)}
    return sorted(selected, key=lambda i: rank.get(i, len(rank))), len(inc)


def top(system, r, k):
    s = system[r]
    return sorted(s, key=lambda i: (-s[i], i))[:k]


def systems_lists():
    haiku = _llm("haiku_b20_low")["llm:haiku_b20_low"]
    emb = _emb("bge-small")["emb:bge-small:whole"]
    grade = {r: {i: s["rel"] for i, s in scores[r].items() if ok(s)} for r in READERS}
    rnd = random.Random(1)
    ids = list(corpus)
    out = {}
    for r in READERS:
        out.setdefault("random", {})[r] = rnd.sample(ids, 40)  # same draw as the reference pool's random posts
    for r in READERS:
        for L in LIMITS:
            out.setdefault(f"agora@{L}", {})[r] = digest(r, L)[0]
            out.setdefault(f"agora_no_flags@{L}", {})[r] = digest(r, L, use_flags=False)[0]
        out.setdefault("decision_grade_only@10", {})[r] = top(grade, r, 10)
        out.setdefault("haiku_per_reader@10", {})[r] = top(haiku, r, 10)
        out.setdefault("embeddings@10", {})[r] = top(emb, r, 10)
    return out


def extra_path():
    return HERE / "sim_labels.json"


async def label():
    lists = systems_lists()
    need = {r: sorted({i for s in lists.values() for i in s[r]} - set(gold[r])) for r in READERS}
    print("pairs to grade:", sum(map(len, need.values())))
    async def rel(r, chunk):
        keyed = [(f"p{n}", corpus[i]) for n, i in enumerate(chunk)]
        blocks = [{"type": "text", "text": "READER PROFILE:\n" + READERS[r] + "\n\nPOSTS:"}] + await opus.post_blocks(keyed)
        res, _ = await opus.call("pitch:sim-rel", REL_SYSTEM, blocks, REL_SCHEMA)
        return r, {chunk[int(x["post_key"][1:])]: x for x in (res or {}).get("items", []) if x["post_key"][1:].isdigit() and int(x["post_key"][1:]) < len(chunk)}
    jobs = []
    for r, ids in need.items():
        random.Random(r).shuffle(ids)
        jobs += [rel(r, ids[j:j + 15]) for j in range(0, len(ids), 15)]
    out = {r: {} for r in READERS}
    for r, d in await asyncio.gather(*jobs):
        out[r].update(d)
    qids = sorted({i for n, s in lists.items() if n != "random" for r in READERS for i in s[r]} | {i for r in READERS for i in lists["random"][r]})
    async def q(chunk):
        keyed = [(f"p{n}", corpus[i]) for n, i in enumerate(chunk)]
        res, _ = await opus.call("pitch:sim-q", Q_SYSTEM, await opus.post_blocks(keyed), Q_SCHEMA)
        return {chunk[int(x["post_key"][1:])]: x for x in (res or {}).get("items", []) if x["post_key"][1:].isdigit() and int(x["post_key"][1:]) < len(chunk)}
    qa = {}
    for d in await asyncio.gather(*(q(qids[j:j + 20]) for j in range(0, len(qids), 20))):
        qa.update(d)
    extra_path().write_text(json.dumps({"rel": out, "q": qa}))
    print(f"anthropic spent ${opus.anthropic_spent():.2f}")


def report():
    lists = systems_lists()
    ex = json.loads(extra_path().read_text())
    FL = ["clickbait", "ragebait", "misinformation", "spam_promo"]
    def g(r, i):
        v = gold[r].get(i) or ex["rel"][r].get(i)
        return v["grade"] if v else None
    rows = {}
    for name, s in lists.items():
        gs = [g(r, i) for r in READERS for i in s[r]]
        gs = [x for x in gs if x is not None]
        qs = [ex["q"][i] for r in READERS for i in s[r] if i in ex["q"]]
        per = [sum((g(r, i) or 0) >= 2 for i in s[r]) / max(1, len(s[r])) for r in READERS]
        rows[name] = dict(n=len(gs), relevant=sum(x >= 2 for x in gs) / len(gs), highlight=sum(x == 3 for x in gs) / len(gs),
                          unrelated=sum(x == 0 for x in gs) / len(gs), worst_reader=min(per),
                          bad=sum(any(q[f] for f in FL) for q in qs) / len(qs) if qs else None,
                          low_effort=sum(q["low_effort"] for q in qs) / len(qs) if qs else None,
                          per_reader={r: round(p, 2) for r, p in zip(READERS, per)})
        print(f"{name:24s} n={len(gs):3d} relevant={rows[name]['relevant']:.0%} highlight={rows[name]['highlight']:.0%} "
              f"unrelated={rows[name]['unrelated']:.0%} worst_reader={min(per):.0%} bad={rows[name]['bad'] if rows[name]['bad'] is None else format(rows[name]['bad'], '.0%')} "
              f"low_effort={rows[name]['low_effort'] if rows[name]['low_effort'] is None else format(rows[name]['low_effort'], '.0%')}")
    inc = {r: digest(r, 10)[1] for r in READERS}
    print("posts passing the gate per reader (of 11,502):", inc)
    hidden = sum(prodq.hidden(f) for f in flags.values() if f)
    down = sum(prodq.penalty(f) < 1 for f in flags.values() if f and not prodq.hidden(f))
    print(f"firehose flags: hidden {hidden}/{sum(1 for f in flags.values() if f)}, downranked {down}, flag errors {sum(f is None for f in flags.values())}")
    errs = sum(1 for r in READERS for s in scores[r].values() if not ok(s))
    print("scoring errors:", errs)
    # Gate recall against the reference pool
    rel_tot = passed = 0
    for r in READERS:
        for i, v in gold[r].items():
            if v["grade"] >= 2:
                rel_tot += 1
                s = scores[r].get(i)
                passed += bool(ok(s) and s["want"] >= 0.5 and s["rel"] >= 0.4 and not prodq.hidden(flags.get(i)))
    print(f"gate recall on reference relevant posts: {passed}/{rel_tot} = {passed / rel_tot:.0%}")
    (HERE / "sim_report.json").write_text(json.dumps({"rows": rows, "gate_pass": inc, "gate_recall": passed / rel_tot,
                                                       "hidden": hidden, "downranked": down}, indent=1))


if __name__ == "__main__":
    asyncio.run(label()) if sys.argv[1] == "label" else report()
