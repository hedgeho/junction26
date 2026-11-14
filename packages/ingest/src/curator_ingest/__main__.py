import argparse
import asyncio
import logging


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="curator-ingest")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="Jetstream consumer + follow sync, forever")
    one = sub.add_parser("sync-follows", help="refresh follows for one reader now")
    one.add_argument("did")
    rates = sub.add_parser("rates", help="print live Jetstream event rates")
    rates.add_argument("seconds", type=float, nargs="?", default=60)
    imported = sub.add_parser("import-posts", help="import normalized public Bluesky/X source records")
    imported.add_argument("path", help="UTF-8 JSON array supplied by the data pipeline")
    args = parser.parse_args()

    from . import follows, jetstream

    if args.cmd == "import-posts":
        import json
        from pathlib import Path

        from curator_core import db

        from .imports import import_posts

        records = json.loads(Path(args.path).read_text(encoding="utf-8"))
        print("Imported", import_posts(db.connect(), records), "public source records")
    elif args.cmd == "run":

        async def both():
            await asyncio.gather(jetstream.run(), follows.run())

        asyncio.run(both())
    elif args.cmd == "sync-follows":
        from curator_core import db

        follows.sync_follows(db.connect(), args.did)
    elif args.cmd == "rates":
        asyncio.run(jetstream.run(stats_only=True, duration_s=args.seconds))


if __name__ == "__main__":
    main()
