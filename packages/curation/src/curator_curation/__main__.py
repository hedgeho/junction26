import argparse
import logging

from curator_core import db

from . import worker


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("curator_curation.worker").setLevel(logging.INFO)
    parser = argparse.ArgumentParser(prog="curator-curate")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run")
    sub.add_parser("timings", help="Latest per-stage curation timings for each reader")
    sub.add_parser("check-providers", help="Synthetic live checks using configured credentials")
    once = sub.add_parser("once")
    once.add_argument("--user", help="Only this reader's Bluesky DID")
    args = parser.parse_args()
    if args.cmd == "check-providers":
        from .config import settings
        from .llm.gemini import PublicCandidate, PublicScoringRequest
        from .llm.gemma import GemmaClient

        cfg = settings()
        try:
            public = worker.make_scorer(cfg)
            public.score(
                PublicScoringRequest(
                    "# Interests\nCompiler research",
                    (PublicCandidate("x:synthetic", "x", "A reproducible compiler benchmark", "", False, False),),
                )
            )
            private = GemmaClient(cfg.gemma_url, cfg.gemma_token, cfg.gemma_model, cfg.inference_timeout_s)
            private.update_profile("# Interests\nCompilers", "Prefer detailed technical results")
            private.match(
                {"text": "A compiler benchmark", "context": ""},
                [{"chunk_id": "synthetic", "text": "Original compiler benchmarking thoughts"}],
                "",
            )
        except Exception:  # noqa: BLE001 - never echo private provider errors
            parser.exit(1, "Provider check failed; verify configuration and endpoint health.\n")
        print("Gemini and Gemma checks passed.")
    elif args.cmd == "timings":
        conn = db.connect()
        for row in conn.execute("SELECT key,value FROM kv WHERE key LIKE 'curation-timing:%' ORDER BY key"):
            print(row["key"].removeprefix("curation-timing:"), row["value"])
    elif args.cmd == "run":
        worker.run_forever()
    else:
        worker.run_cycle(db.connect(), only_did=args.user)


if __name__ == "__main__":
    main()
