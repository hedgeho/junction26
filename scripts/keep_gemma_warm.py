"""Keep the configured Gemma endpoint active for a bounded demo window.

Run from the repository root; credentials come from the normal application settings.
Only synthetic requests are sent. This process requires the host to remain awake.
"""

import argparse
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from curator_curation.config import settings
from curator_curation.llm.gemma import GemmaClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=float, default=4)
    parser.add_argument("--state-file", type=Path, default=Path("data/gemma-keepwarm.json"))
    args = parser.parse_args()
    if not 0 < args.hours <= 24:
        parser.error("--hours must be greater than zero and at most 24")
    cfg = settings()
    if not cfg.gemma_url or not cfg.gemma_token:
        raise SystemExit("Gemma is not configured")
    args.state_file.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + args.hours * 3600
    state = {
        "pid": os.getpid(),
        "started_at": datetime.now(UTC).isoformat(),
        "ends_at": datetime.fromtimestamp(time.time() + args.hours * 3600, UTC).isoformat(),
        "interval_seconds": 60,
        "successful_pings": 0,
        "failed_pings": 0,
    }

    def save():
        temporary = args.state_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
        temporary.replace(args.state_file)

    warmed, attempts = False, 0
    save()
    with httpx.Client(timeout=120) as client:
        while time.monotonic() < deadline:
            cycle = time.monotonic()
            try:
                response = client.post(
                    cfg.gemma_url.rstrip("/") + "/chat/completions",
                    headers={"Authorization": "Bearer " + cfg.gemma_token},
                    json={
                        "model": cfg.gemma_model,
                        "temperature": 0,
                        "max_tokens": 1,
                        "messages": [{"role": "user", "content": "Say yes."}],
                    },
                )
                response.raise_for_status()
                state["successful_pings"] += 1
                state["last_status"] = "warm"
                state["last_latency_seconds"] = round(time.monotonic() - cycle, 3)
                state.pop("last_error_type", None)
            except Exception as exc:  # noqa: BLE001 - never log provider data or credentials
                state["failed_pings"] += 1
                state["last_status"] = "retrying"
                state["last_error_type"] = type(exc).__name__
            state["last_checked_at"] = datetime.now(UTC).isoformat()
            save()
            if not warmed and attempts < 3 and state["last_status"] == "warm":
                attempts += 1
                try:
                    GemmaClient(cfg.gemma_url, cfg.gemma_token, cfg.gemma_model, 120).warmup(
                        scoring=cfg.scoring_backend == "gemma"
                    )
                    warmed = state["formats_warmed"] = True
                    state.pop("warmup_error_type", None)
                except Exception as exc:  # noqa: BLE001
                    state["warmup_error_type"] = type(exc).__name__
                save()
            delay = min(max(0, 60 - (time.monotonic() - cycle)), max(0, deadline - time.monotonic()))
            time.sleep(delay)
    state["last_status"] = "finished"
    save()


if __name__ == "__main__":
    main()
