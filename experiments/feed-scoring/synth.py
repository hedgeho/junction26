"""Seeded bad-content test set with known labels: realistic clickbait / misinformation / ragebait / spam posts
plus benign look-alikes on the same topics (hard negatives). Written by Sonnet for classifier evaluation."""

import asyncio
import json

from common import DATA, MODELS, call_json, gather_limited

SYSTEM = """You help build an evaluation set for a content-quality classifier in a Bluesky feed curator.
Write realistic, short (<300 chars) social posts as they would actually appear. No hashtags spam unless
the category calls for it. Vary topics: science, health, politics, tech, AI, climate, books, games, cities."""

KINDS = {
    "clickbait": "clickbait posts: curiosity-gap teasers, exaggerated headlines withholding the point (with a link title)",
    "misinformation": "posts that confidently assert well-known false or misleading claims (common health myths, "
                      "debunked conspiracy theories, fabricated statistics, false science claims)",
    "ragebait": "posts engineered to provoke outrage/dunking, sweeping insulting generalizations, no new information",
    "spam_promo": "spam/promo: follow-for-follow, crypto/giveaway scams, 'my book is FREE today' blasts, engagement farming",
    "benign_news": "sober, factual news/link posts on the same kinds of topics, with link titles that state the point",
    "benign_opinion": "strongly worded but substantive, good-faith opinions or critiques with actual arguments",
    "benign_promo": "legit announcements: a researcher sharing their paper, an indie dev sharing a launch, honest and specific",
    "benign_correction": "posts that debunk a common myth correctly, mentioning the myth",
}

SCHEMA = {
    "type": "object",
    "properties": {"posts": {"type": "array", "items": {
        "type": "object",
        "properties": {"text": {"type": "string"}, "link_title": {"type": "string"}},
        "required": ["text", "link_title"], "additionalProperties": False}}},
    "required": ["posts"], "additionalProperties": False,
}


async def main():
    res = await gather_limited([
        call_json("synth", MODELS["sonnet"], SYSTEM,
                  f"Write 20 distinct {desc}. link_title is the title of the attached link card, or empty string.",
                  SCHEMA, effort="low")
        for desc in KINDS.values()])
    with (DATA / "synth.jsonl").open("w") as f:
        for kind, (out, meta) in zip(KINDS, res):
            for i, p in enumerate(out["posts"]):
                f.write(json.dumps({"id": f"synth:{kind}:{i}", "author": "synth", "text": p["text"],
                                    "is_reply": False, "embed": "external" if p["link_title"] else None,
                                    "link": p["link_title"] or None, "label": kind}) + "\n")
            print(kind, len(out["posts"]), f"${meta['cost']:.3f}")


asyncio.run(main())
