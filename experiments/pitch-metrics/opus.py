"""Opus labeler with optional images. Shares the feed-scoring cache and ledger; Anthropic-only spend cap."""
import asyncio, base64, hashlib, json, time
import httpx
import prodq  # noqa: F401  (sets sys.path)
import common
from common import CACHE, LEDGER, PRICE, MODELS

ANTHROPIC_CAP_USD = 40.0
OPUS = MODELS["opus"]
_img_http = None


def anthropic_spent():
    return sum(json.loads(l)["cost"] for l in LEDGER.read_text().splitlines() if l.strip() and "/" not in json.loads(l)["model"])


async def image_b64(url):
    global _img_http
    if _img_http is None:
        _img_http = httpx.AsyncClient(timeout=20, follow_redirects=True)
    path = CACHE / f"img_{hashlib.sha256(url.encode()).hexdigest()[:24]}.bin"
    if path.exists():
        data = path.read_bytes()
    else:
        try:
            r = await _img_http.get(url)
            r.raise_for_status()
            data = r.content
        except httpx.HTTPError:
            data = b""
        path.write_bytes(data)
    if not data or len(data) > 4_000_000:
        return None
    mt = "image/png" if data[:4] == b"\x89PNG" else "image/webp" if data[8:12] == b"WEBP" else "image/jpeg"
    return mt, base64.b64encode(data).decode()


async def call(tag, system, blocks, schema, effort="high", max_tokens=16000):
    params = dict(model=OPUS, max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": blocks}],
                  output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}})
    h = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:24]
    path = CACHE / f"{h}.json"
    if path.exists():
        d = json.loads(path.read_text())
        return d["out"], d["meta"] | {"cached": True}
    if anthropic_spent() > ANTHROPIC_CAP_USD:
        raise RuntimeError(f"anthropic cap reached: ${anthropic_spent():.2f}")
    t = time.monotonic()
    resp = await common.client().messages.create(**params)
    pi, po = PRICE[OPUS]
    u = resp.usage
    meta = {"tag": tag, "model": OPUS, "in": u.input_tokens, "out": u.output_tokens,
            "cost": (u.input_tokens * pi + u.output_tokens * po) / 1e6, "latency_s": time.monotonic() - t, "stop": resp.stop_reason}
    with LEDGER.open("a") as f:
        f.write(json.dumps(meta) + "\n")
    if resp.stop_reason == "refusal":
        return None, meta
    out = json.loads(next(b.text for b in resp.content if b.type == "text"))
    path.write_text(json.dumps({"out": out, "meta": meta}))
    return out, meta


async def post_blocks(items):
    """items: list of (key, post dict with text/is_reply/link/link_desc/image)."""
    blocks = []
    for key, p in items:
        d = {"post_key": key, "text": p["text"], "is_reply": p["is_reply"]}
        if p.get("link"):
            d["link_card"] = {"title": p["link"], "description": p.get("link_desc", "")}
        img = await image_b64(p["image"]) if p.get("image") else None
        d["has_image"] = bool(img)
        blocks.append({"type": "text", "text": json.dumps(d, ensure_ascii=False)})
        if img:
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": img[0], "data": img[1]}})
    return blocks
