"""Sample English Bluesky posts from Jetstream into data/posts.jsonl."""

import asyncio
import json
import sys
import time

import websockets

URL = "wss://jetstream2.us-east.bsky.network/subscribe?wantedCollections=app.bsky.feed.post"


async def main(seconds: int):
    out = open("data/posts.jsonl", "w")
    n = kept = 0
    end = time.time() + seconds
    async with websockets.connect(URL, max_size=2**22) as ws:
        while time.time() < end:
            evt = json.loads(await ws.recv())
            c = evt.get("commit") or {}
            if evt.get("kind") != "commit" or c.get("operation") != "create":
                continue
            n += 1
            r = c.get("record") or {}
            text = (r.get("text") or "").strip()
            if "en" not in (r.get("langs") or []) or len(text) < 40:
                continue
            embed = (r.get("embed") or {}).get("$type", "")
            out.write(
                json.dumps(
                    {
                        "id": f"at://{evt['did']}/app.bsky.feed.post/{c['rkey']}",
                        "author": evt["did"],
                        "text": text,
                        "is_reply": "reply" in r,
                        "embed": embed.rsplit(".", 1)[-1] if embed else None,
                        "link": ((r.get("embed") or {}).get("external") or {}).get("title"),
                    }
                )
                + "\n"
            )
            kept += 1
            if kept % 2000 == 0:
                print(f"seen {n} kept {kept}", flush=True)
    print(f"done: seen {n} kept {kept}")


asyncio.run(main(int(sys.argv[1])))
