"""Build the read-only archive DB served with ARCHIVED_ON set.

    uv run --with freezegun python scripts/build_archive.py SNAPSHOT OUT --demo-did DID --at 2026-11-15T09:36:11Z

Copies SNAPSHOT to OUT, stores the demo reader's funnel as computed at --at (the funnel counts
against the current day, so it can't be computed live later), then deletes everything the archived
demo doesn't show: other readers, notes, profiles, feedback, sessions and posts outside the demo's
digests. Run it from a directory without a .env, with the curation settings production used
(e.g. HIDE_SHOWN_POSTS=false) in the environment.
"""

import argparse
import json
import sqlite3
from datetime import datetime

from freezegun import freeze_time

ARCHIVED_FUNNEL_KEY = "archive:demo-funnel"  # curator_web.site reads it
# Tables the archive serves, filtered to the demo reader below. Every other table is emptied.
KEPT = {"reader_accounts", "reader_settings", "users", "digest_runs", "digest_items", "posts", "social_posts", "kv"}


def copy(src: str, dst: str) -> sqlite3.Connection:
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    out = sqlite3.connect(dst)
    source.backup(out)
    source.close()
    out.row_factory = sqlite3.Row
    return out


def store_funnel(conn, uid: str, did: str, at: datetime) -> dict:
    from curator_curation import digests, funnel

    with freeze_time(at):
        result = funnel.compute(conn, uid, did)
        shown = len(digests.read(conn, uid))
    delivered = next((s["count"] for s in result["stages"] if s["key"] == "delivered"), 0)
    if not shown or delivered != shown:
        raise SystemExit(f"funnel delivered {delivered} posts but the demo shows {shown}; check --at and settings")
    conn.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (ARCHIVED_FUNNEL_KEY, json.dumps(result)))
    conn.commit()
    return result


def strip(conn, uid: str, did: str) -> None:
    keys = {
        r[0]
        for r in conn.execute(
            "SELECT i.post_key FROM digest_items i JOIN digest_runs d ON d.id=i.run_id WHERE d.user_id=?", (uid,)
        )
    }
    conn.execute("CREATE TEMP TABLE keep(post_key TEXT PRIMARY KEY)")
    conn.executemany("INSERT INTO keep VALUES (?)", [(k,) for k in keys])
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND sql NOT LIKE '%VIRTUAL%'"
            " AND name NOT LIKE '%fts_%' AND name<>'schema_migrations'"
        )
    ]
    for table in tables:
        if table not in KEPT:
            conn.execute(f"DELETE FROM {table}")
    conn.execute("DELETE FROM reader_accounts WHERE id<>?", (uid,))
    conn.execute("DELETE FROM reader_settings WHERE user_id<>?", (uid,))
    conn.execute("DELETE FROM users WHERE did<>?", (did,))
    conn.execute("DELETE FROM digest_runs WHERE user_id<>?", (uid,))
    conn.execute("DELETE FROM digest_items WHERE run_id NOT IN (SELECT id FROM digest_runs)")
    conn.execute("DELETE FROM kv WHERE key NOT IN (?,?)", ("setup:" + uid, ARCHIVED_FUNNEL_KEY))
    conn.execute("DELETE FROM posts WHERE 'bluesky:' || uri NOT IN (SELECT post_key FROM keep)")
    conn.execute("DELETE FROM social_posts WHERE platform || ':' || native_id NOT IN (SELECT post_key FROM keep)")
    for fts in ("posts_fts", "social_posts_fts"):
        conn.execute(f"INSERT INTO {fts}({fts}) VALUES ('rebuild')")
    conn.commit()
    conn.execute("VACUUM")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("snapshot")
    parser.add_argument("out")
    parser.add_argument("--demo-did", required=True)
    parser.add_argument("--at", required=True, type=datetime.fromisoformat, help="moment the snapshot was taken")
    args = parser.parse_args()

    conn = copy(args.snapshot, args.out)
    row = conn.execute("SELECT id FROM reader_accounts WHERE bluesky_did=?", (args.demo_did,)).fetchone()
    if not row:
        raise SystemExit("demo reader not found in snapshot")
    uid = row["id"]
    result = store_funnel(conn, uid, args.demo_did, args.at)
    strip(conn, uid, args.demo_did)
    for stage in result["stages"]:
        print(f"{stage['label']:45} {stage['count']}")
    print("tables:", {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in sorted(KEPT)})


if __name__ == "__main__":
    main()
