"""Shared Claude client with structured output, on-disk response cache, cost ledger and budget guard."""

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path

import anthropic
import httpx

ROOT = Path(__file__).parent
DATA = ROOT / "data"
CACHE = DATA / "cache"
CACHE.mkdir(parents=True, exist_ok=True)
LEDGER = DATA / "ledger.jsonl"
BUDGET_USD = 29.0

MODELS = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5",
          "jev": "typesafe/jev-router", "auto": "openrouter/auto", "autobeta": "openrouter/auto-beta",
          "dsflash": "deepseek/deepseek-v4.1-flash-1017",
          "luna": "openai/gpt-6-luna", "dspro": "deepseek/deepseek-v4-pro-0813"}
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
PRICE = {  # $/MTok input, output
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}


def _key(name="ANTHROPIC_API_KEY"):
    env = ROOT.parent / ".env"
    for line in env.read_text().splitlines() if env.exists() else []:
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip().strip("'\"")
    return os.environ[name]


class Refusal(Exception):
    def __init__(self, meta):
        super().__init__("refusal")
        self.meta = meta


async def split_on_refusal(fn, batch, metas=None):
    """fn(batch) -> (dict, meta). On refusal, bisect; a single refused item maps to None."""
    metas = [] if metas is None else metas
    try:
        res, meta = await fn(batch)
        metas.append(meta)
        return res, metas
    except Refusal as e:
        metas.append(e.meta | {"refused": True})
        if len(batch) == 1:
            return {batch[0]["id"]: None}, metas
        mid = len(batch) // 2
        a, _ = await split_on_refusal(fn, batch[:mid], metas)
        b, _ = await split_on_refusal(fn, batch[mid:], metas)
        return a | b, metas


_client = None


def client():
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=_key(), max_retries=8, timeout=300)
    return _client


def spent() -> float:
    if not LEDGER.exists():
        return 0.0
    return sum(json.loads(l)["cost"] for l in LEDGER.read_text().splitlines() if l.strip())


def posts():
    return [json.loads(l) for l in (DATA / "posts.jsonl").read_text().splitlines()]


_http = None


async def _openrouter(params):
    """OpenRouter chat completion with strict JSON schema. Returns (text, meta-without-tag)."""
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=300, headers={"Authorization": f"Bearer {_key('OPENROUTER_API_KEY')}"})
    body = {
        "model": params["model"],
        "max_tokens": params["max_tokens"],
        "messages": [{"role": "system", "content": params["system"]}, *params["messages"]],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "out", "strict": True, "schema": params["output_config"]["format"]["schema"]}},
        "usage": {"include": True},
    }
    if params["output_config"].get("effort"):
        body["reasoning"] = {"effort": params["output_config"]["effort"]}
    for attempt in range(6):
        t = time.monotonic()
        r = await _http.post(OPENROUTER_URL, json=body)
        lat = time.monotonic() - t
        if r.status_code in (429, 500, 502, 503, 504) or (r.status_code == 200 and "error" in r.json()):
            await asyncio.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        d = r.json()
        ch = d["choices"][0]
        u = d.get("usage") or {}
        return ch["message"].get("content") or "", {
            "model": params["model"], "routed": d.get("model"), "provider": d.get("provider"),
            "in": u.get("prompt_tokens", 0), "out": u.get("completion_tokens", 0),
            "reasoning_tokens": (u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0),
            "cost": u.get("cost", 0.0), "latency_s": lat,
            "stop": "refusal" if ch["message"].get("refusal") else ch.get("finish_reason")}
    raise RuntimeError(f"openrouter failed: {r.status_code} {r.text[:300]}")


def _normalize(out, schema):
    """Some OpenRouter-served models ignore strict mode and return the bare item array."""
    if isinstance(out, list) and list(schema.get("properties", {})) == ["items"]:
        return {"items": out}, True
    return out, False


async def call_json(tag: str, model: str, system: str, user: str, schema: dict, effort: str = "low",
                    thinking: dict | None = None, max_tokens: int = 16000) -> tuple[dict, dict]:
    """Returns (parsed_json, meta). meta has latency_s, cost, tokens, cached."""
    effort = None if effort == "none" else effort
    params = dict(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
    )
    if thinking:
        params["thinking"] = thinking
    h = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:24]
    path = CACHE / f"{h}.json"
    if path.exists():
        d = json.loads(path.read_text())
        if d.get("refusal"):
            raise Refusal(d["meta"] | {"cached": True})
        out, fixed = _normalize(d["out"], schema)
        return out, d["meta"] | {"cached": True, "schema_fixed": fixed}
    if spent() > BUDGET_USD:
        raise RuntimeError(f"budget exhausted: ${spent():.2f}")
    if "/" in model:
        for attempt in range(3):
            text, m = await _openrouter(params)
            meta = {"tag": tag, **m}
            with LEDGER.open("a") as f:
                f.write(json.dumps(meta) + "\n")
            if meta["stop"] == "refusal":
                path.write_text(json.dumps({"refusal": True, "meta": meta}))
                raise Refusal(meta)
            try:
                out, meta["schema_fixed"] = _normalize(json.loads(text), schema)
                break
            except json.JSONDecodeError:
                continue
        else:
            raise RuntimeError(f"unparseable output from {meta['routed']}: {text[:200]!r}")
        path.write_text(json.dumps({"out": out, "meta": meta}))
        return out, meta | {"cached": False}
    t = time.monotonic()
    resp = await client().messages.create(**params)
    lat = time.monotonic() - t
    pi, po = PRICE[model]
    u = resp.usage
    cost = (u.input_tokens * pi + u.output_tokens * po) / 1e6
    meta = {"tag": tag, "model": model, "in": u.input_tokens, "out": u.output_tokens, "cost": cost,
            "latency_s": lat, "stop": resp.stop_reason}
    with LEDGER.open("a") as f:
        f.write(json.dumps(meta) + "\n")
    if resp.stop_reason == "refusal":
        path.write_text(json.dumps({"refusal": True, "meta": meta}))
        raise Refusal(meta)
    text = next(b.text for b in resp.content if b.type == "text")
    out = json.loads(text)
    path.write_text(json.dumps({"out": out, "meta": meta}))
    return out, meta | {"cached": False}


async def gather_limited(coros, limit=16):
    sem = asyncio.Semaphore(limit)

    async def run(c):
        async with sem:
            return await c

    return await asyncio.gather(*(run(c) for c in coros))
