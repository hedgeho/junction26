"""Seeded bad-content set (labels known by construction) through the production triage questions and thresholds."""
import asyncio, json
from collections import Counter
import prodq
from common import DATA

async def main():
    items = [json.loads(l) for l in (DATA / "synth.jsonl").read_text().splitlines()]
    res = await asyncio.gather(*(prodq.flags(p, "pitch:seeded") for p in items))
    c = Counter()
    for p, (f, _) in zip(items, res):
        bad = p["label"] in ("clickbait", "ragebait", "misinformation", "spam")
        hid = prodq.hidden(f)
        act = hid or prodq.penalty(f) < 1
        c[(p["label"], "hidden")] += hid
        c[(p["label"], "hidden_or_downranked")] += act
        c[(p["label"], "n")] += 1
    for lab in sorted({k[0] for k in c}):
        print(f"{lab:28s} n={c[(lab,'n')]:3d} hidden={c[(lab,'hidden')]:3d} hidden_or_downranked={c[(lab,'hidden_or_downranked')]:3d}")
asyncio.run(main())
