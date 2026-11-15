"""Measure authenticated reads against an isolated copy of real reader data.

uv run --with playwright python scripts/check_reader_performance.py

Uses check_reader_ui's temporary database and blocked social writes. This measures
local read latency, not inference speed or production capacity. Requires the same
real source data as the browser audit. Output: data/reader-performance.json.
"""

import asyncio
import json
import math
import statistics
import time
from pathlib import Path

import check_reader_ui as audit
import httpx


def summarize(samples):
    ordered = sorted(samples)
    return {
        "requests": len(samples),
        "median_ms": round(statistics.median(samples), 1),
        "p95_ms": round(ordered[math.ceil(len(samples) * 0.95) - 1], 1),
    }


async def measure():
    report = {}
    async with httpx.AsyncClient(base_url=audit.ORIGIN, follow_redirects=True, timeout=30) as client:
        await client.get("/__audit/sign-in")
        for path in [
            "/me",
            "/settings",
            "/api/curation",
            "/api/note-matches",
            "/api/funnel",
            "/settings/history/feed-curation",
        ]:
            samples = []
            for _ in range(15):
                started = time.perf_counter()
                response = await client.get(path)
                response.raise_for_status()
                samples.append((time.perf_counter() - started) * 1000)
            report[path] = summarize(samples)
        semaphore = asyncio.Semaphore(8)

        async def request():
            async with semaphore:
                started = time.perf_counter()
                response = await client.get("/me")
                response.raise_for_status()
                assert response.text.count('class="post"') == 10
                return (time.perf_counter() - started) * 1000

        started = time.perf_counter()
        samples = await asyncio.gather(*(request() for _ in range(40)))
        report["concurrent_feed"] = {
            **summarize(samples),
            "concurrency": 8,
            "total_seconds": round(time.perf_counter() - started, 3),
            "errors": 0,
        }
    target = Path(__file__).resolve().parents[1] / "data/reader-performance.json"
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    audit.main = measure
    asyncio.run(audit.run_isolated())
