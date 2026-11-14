import logging
import time

from curator_core import db

log = logging.getLogger(__name__)


def prune(conn, retention_hours: int) -> int:
    cutoff = int(time.time()) - retention_hours * 3600
    with db.tx(conn):
        n = conn.execute("DELETE FROM posts WHERE indexed_at < ?", (cutoff,)).rowcount
    log.info("pruned %d posts older than %dh", n, retention_hours)
    return n
