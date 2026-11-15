"""Score every corpus post for every synthetic reader with the production questions, and flag every post once."""
import asyncio, json, sys, time
from pathlib import Path
import prodq
from readers import READERS

OUT = Path(__file__).parent / "data"
OUT.mkdir(exist_ok=True)


async def main():
    prodq.decide.SEM = asyncio.Semaphore(int(sys.argv[1]) if len(sys.argv) > 1 else 32)
    corpus = [json.loads(l) for l in (Path(prodq.decide.DATA) / "corpus.jsonl").read_text().splitlines()]
    t0, done, metas = time.monotonic(), 0, []

    async def tick(coro):
        nonlocal done
        r = await coro
        done += 1
        if done % 2000 == 0:
            print(f"{done} done {time.monotonic() - t0:.0f}s ${sum(m['cost'] for m in metas if not m.get('cached')):.2f}", flush=True)
        return r

    async def fl(p):
        try:
            f, m = await prodq.flags(p, "pitch:flags")
        except RuntimeError as e:
            return p["id"], {"error": str(e)[:120]}
        metas.append(m)
        return p["id"], f

    async def sc(r, p):
        try:
            s, m = await prodq.score(READERS[r], p, "pitch:full")
        except RuntimeError as e:  # sensitive-content rejections etc.
            return r, p["id"], {"error": str(e)[:120]}
        metas.append(m)
        return r, p["id"], s

    flags = dict(await asyncio.gather(*(tick(fl(p)) for p in corpus)))
    (OUT / "flags.json").write_text(json.dumps(flags))
    res = await asyncio.gather(*(tick(sc(r, p)) for r in READERS for p in corpus))
    scores = {r: {} for r in READERS}
    for r, i, s in res:
        scores[r][i] = s
    (OUT / "full_scores.json").write_text(json.dumps(scores))
    lat = sorted(m["latency_s"] for m in metas if not m.get("cached"))
    print(json.dumps({"requests": len(metas), "cost": sum(m["cost"] for m in metas if not m.get("cached")),
                      "p50": lat[len(lat) // 2] if lat else None, "p90": lat[int(len(lat) * .9)] if lat else None,
                      "wall_s": time.monotonic() - t0}))
    (OUT / "full_meta.json").write_text(json.dumps([{k: m.get(k) for k in ("tag", "cost", "latency_s", "in", "cached")} for m in metas]))


asyncio.run(main())
