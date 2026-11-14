import logging
import time

from curator_core import db

log = logging.getLogger(__name__)


def prune(conn, retention_hours: int) -> int:
    cutoff = int(time.time()) - retention_hours * 3600
    with db.tx(conn):
        n = conn.execute("DELETE FROM posts WHERE indexed_at < ?", (cutoff,)).rowcount
        n += conn.execute("DELETE FROM social_posts WHERE observed_at < ? AND deleted=0", (cutoff,)).rowcount
        # Keep normalized tombstones while a legacy row exists, so it cannot reappear.
        conn.execute(
            """DELETE FROM social_posts WHERE observed_at<? AND deleted=1
            AND NOT EXISTS (SELECT 1 FROM posts p WHERE social_posts.platform='bluesky' AND p.uri=social_posts.native_id)""",
            (cutoff,),
        )
    log.info("pruned %d posts older than %dh", n, retention_hours)
    return n
