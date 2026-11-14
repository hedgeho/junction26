import argparse
import logging


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="curator-curate")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="scoring/ranking loop, forever")
    once = sub.add_parser("once", help="one cycle, then exit")
    once.add_argument("--user", help="only this reader's DID")
    args = parser.parse_args()

    from . import worker

    if args.cmd == "run":
        worker.run_forever()
        return

    import anthropic
    from curator_core import db

    from .config import settings
    from .rank import rank_user
    from .readers import ensure_user

    conn = db.connect()
    client = anthropic.Anthropic(api_key=settings().anthropic_api_key or None)
    if args.user:
        ensure_user(conn, args.user)
        if not conn.execute("SELECT 1 FROM follow_sync WHERE user_did = ?", (args.user,)).fetchone():
            print(f"note: follows not synced yet; run `curator-ingest sync-follows {args.user}`")
        print("scored", worker.score_user(conn, client, args.user), "ranked", rank_user(conn, args.user))
    else:
        worker.run_cycle(conn, client)


if __name__ == "__main__":
    main()
