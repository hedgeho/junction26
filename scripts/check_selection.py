"""Read-only snapshot of each reader's feedback jobs, profile head and digest runs."""

import sqlite3
import time

c = sqlite3.connect("file:/app/data/curator.db?mode=ro", uri=True)
c.row_factory = sqlite3.Row
now = int(time.time())


def ago(t):
    return f"{(now - t) // 60}m ago" if t else "-"


for r in c.execute("SELECT a.id, u.handle FROM reader_accounts a LEFT JOIN users u ON u.did=a.bluesky_did"):
    uid = r["id"]
    print(f"\n=== {r['handle']} ({uid})")
    s = c.execute("SELECT version, refresh_requested FROM reader_settings WHERE user_id=?", (uid,)).fetchone()
    print("settings:", dict(s) if s else None)
    print("feedback jobs:")
    for j in c.execute(
        "SELECT kind,state,attempts,error_code,applied_revision,created_at,substr(feedback,1,60) f FROM feedback_jobs"
        " WHERE user_id=? AND source_chunk_id IS NULL ORDER BY created_at",
        (uid,),
    ):
        print(
            f"  {ago(j['created_at']):>8} {j['kind']:7} {j['state']:10} att={j['attempts']} "
            f"err={j['error_code']} rev={(j['applied_revision'] or '-')[:8]} {j['f']!r}"
        )
    print("profile revisions:")
    head = c.execute("SELECT revision_id FROM profile_heads WHERE user_id=? AND kind='public'", (uid,)).fetchone()
    for p in c.execute(
        "SELECT id,origin,created_at FROM profile_revisions WHERE user_id=? AND kind='public' ORDER BY created_at",
        (uid,),
    ):
        mark = " <- HEAD" if head and p["id"] == head[0] else ""
        print(f"  {ago(p['created_at']):>8} {p['id'][:8]} {p['origin']}{mark}")
    print("digest runs (latest 5):")
    for d in c.execute(
        "SELECT d.*, (SELECT count(*) FROM digest_items i WHERE i.run_id=d.id) n FROM digest_runs d"
        " WHERE user_id=? ORDER BY created_at DESC LIMIT 5",
        (uid,),
    ):
        stale = "" if head and d["profile_revision"] == head[0] else "  (old profile -> hidden)"
        print(
            f"  {ago(d['created_at']):>8} day={d['day']} rev={d['profile_revision'][:8]} "
            f"v={d['settings_version']} items={d['n']}{stale}"
        )
