"""Recover accepted feedback by its reader-scoped idempotency key."""

from curator_core import db
from fastapi import APIRouter, HTTPException, Query, Request

from . import site

router = APIRouter()


@router.get("/api/feedback-status")
def feedback_status(request: Request, key: str = Query(..., min_length=1, max_length=128)):
    conn = db.connect()
    try:
        uid = site.reader(request, conn)
        job = conn.execute(
            "SELECT * FROM feedback_jobs WHERE user_id=? AND idempotency_key=?",
            (uid, key),
        ).fetchone()
        if not job:
            raise HTTPException(404)
        return site.feedback_result(conn, uid, job)
    finally:
        conn.close()
