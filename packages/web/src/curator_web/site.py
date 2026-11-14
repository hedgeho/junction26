"""Source-only reader, quiet feedback jobs, and opt-in configuration access."""

import asyncio
import hmac
import json
import secrets
import time
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from curator_core import db
from curator_curation import digests, jobs, notes, profiles
from curator_curation.readers import ensure_user
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from . import appview, oauth


async def csrf_guard(request: Request):
    if request.method != "POST":
        return
    form = await request.form(max_fields=20, max_files=0, max_part_size=300000)
    received = form.get("csrf", "")
    expected = request.session.get("csrf", "")
    if not isinstance(received, str) or not expected or not hmac.compare_digest(received, expected):
        raise HTTPException(403, "Invalid request")


router = APIRouter(dependencies=[Depends(csrf_guard)])
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def page(request, name, context):
    token = request.session.setdefault("csrf", secrets.token_urlsafe(32))
    return templates.TemplateResponse(request, name, dict(context, csrf=token))


def current_did(request: Request) -> str:
    did = request.session.get("did")
    if not did:
        raise HTTPException(303, headers={"Location": "/"})
    return did


def reader(request: Request, conn) -> str:
    did = current_did(request)
    ensure_user(conn, did)
    return profiles.bootstrap(conn, did)


def redirect(path="/me"):
    return RedirectResponse(path, status_code=303)


@router.get("/oauth-client-metadata.json")
def oauth_client_metadata():
    return oauth.client().client_metadata


@router.get("/oauth/jwks.json")
def oauth_jwks():
    return oauth.client().jwks


@router.post("/login")
async def login(handle: str = Form(..., min_length=1, max_length=253)):
    try:
        url = await oauth.client().authorize(handle.strip().lstrip("@"))
    except Exception:  # noqa: BLE001 - sanitize external provider errors at the boundary
        raise HTTPException(400, "Sign-in failed") from None
    return redirect(url)


@router.get("/oauth/callback")
async def oauth_callback(request: Request):
    try:
        result = await oauth.client().callback(dict(request.query_params))
    except Exception:  # noqa: BLE001 - sanitize external provider errors at the boundary
        raise HTTPException(400, "Sign-in failed") from None
    did = result.session.did
    request.session.clear()
    request.session["did"] = did
    request.session["csrf"] = secrets.token_urlsafe(32)
    c = db.connect()
    try:
        ensure_user(c, did)
        profiles.bootstrap(c, did)
        try:
            handle = (await asyncio.to_thread(appview.get_profile, did)).get("handle")
            c.execute("UPDATE users SET handle=? WHERE did=?", (handle, did))
        except httpx.HTTPError:
            pass
    finally:
        c.close()
    return redirect()


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return redirect("/")


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    if request.session.get("did"):
        return redirect()
    return page(request, "index.html", {})


@router.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request):
    return page(request, "privacy.html", {})


@router.get("/me", response_class=HTMLResponse)
def me(request: Request):
    c = db.connect()
    try:
        uid = reader(request, c)
        user = c.execute("SELECT * FROM users WHERE did=?", (current_did(request),)).fetchone()
        pending = c.execute(
            "SELECT id,state FROM feedback_jobs WHERE user_id=? AND source_chunk_id IS NULL ORDER BY created_at DESC,rowid DESC LIMIT 1",
            (uid,),
        ).fetchone()
        return page(
            request,
            "me.html",
            {
                "user": user,
                "posts": digests.read(c, uid),
                "job": dict(pending) if pending else None,
                "submission_key": uuid.uuid4().hex,
            },
        )
    finally:
        c.close()


@router.get("/api/digest")
def digest_api(request: Request):
    c = db.connect()
    try:
        return {"items": digests.read(c, reader(request, c))}
    finally:
        c.close()


@router.post("/me/feedback")
def feedback(
    request: Request,
    text: str = Form(..., min_length=1, max_length=8000),
    key: str = Form(..., min_length=1, max_length=128),
    kind: str = Form("public"),
):
    c = db.connect()
    try:
        jobs.enqueue(c, reader(request, c), kind, text, key)
    except ValueError:
        raise HTTPException(400, "Invalid feedback") from None
    finally:
        c.close()
    return redirect()


@router.get("/api/jobs/{job_id}")
def job_status(request: Request, job_id: str):
    c = db.connect()
    try:
        uid = reader(request, c)
        row = c.execute("SELECT state FROM feedback_jobs WHERE user_id=? AND id=?", (uid, job_id)).fetchone()
        if not row:
            raise HTTPException(404)
        return {"state": row["state"]}
    finally:
        c.close()


@router.post("/me/refresh")
def refresh(request: Request):
    c = db.connect()
    try:
        c.execute(
            "UPDATE reader_settings SET refresh_requested=1,version=version+1 WHERE user_id=?", (reader(request, c),)
        )
    finally:
        c.close()
    return redirect()


@router.post("/me/dismiss")
def dismiss(request: Request, post_key: str = Form(..., max_length=1000)):
    c = db.connect()
    try:
        uid = reader(request, c)
        if post_key not in {p["key"] for p in digests.read(c, uid)}:
            raise HTTPException(404)
        c.execute("INSERT OR REPLACE INTO dismissed_posts VALUES (?,?,?)", (uid, post_key, int(time.time())))
    finally:
        c.close()
    return redirect()


@router.get("/settings", response_class=HTMLResponse)
def config_page(request: Request):
    c = db.connect()
    try:
        uid = reader(request, c)
        docs = [
            {
                "kind": k,
                "revision": (profiles.head(c, uid, k) or {"id": ""})["id"],
                "body": profiles.markdown(c, uid, k),
            }
            for k in ("public", "private")
        ]
        config = dict(digests.config_for(c, uid))
        config["blocked_authors"] = "\n".join(json.loads(config["blocked_authors"]))
        source_notes = c.execute(
            "SELECT id,title FROM note_versions WHERE user_id=? ORDER BY created_at DESC", (uid,)
        ).fetchall()
        history = c.execute(
            "SELECT id,kind,created_at FROM profile_revisions WHERE user_id=? ORDER BY created_at DESC,rowid DESC LIMIT 20",
            (uid,),
        ).fetchall()
        return page(
            request, "settings.html", {"docs": docs, "config": config, "notes": source_notes, "history": history}
        )
    finally:
        c.close()


@router.get("/settings/profile/{kind}")
def download_profile(request: Request, kind: str):
    if kind not in profiles.KINDS:
        raise HTTPException(404)
    c = db.connect()
    try:
        body = profiles.markdown(c, reader(request, c), kind)
        return PlainTextResponse(
            body,
            headers={"Content-Disposition": f'attachment; filename="{kind}-profile.md"', "Cache-Control": "no-store"},
        )
    finally:
        c.close()


@router.post("/settings/profile/{kind}")
def edit_profile(request: Request, kind: str, body: str = Form(..., max_length=24000), revision: str = Form("")):
    c = db.connect()
    try:
        # Drop derived retrieval terms on a manual edit rather than applying stale terms.
        profiles.save(c, reader(request, c), kind, body, revision or None)
    except profiles.RevisionConflict:
        raise HTTPException(409, "Configuration changed; reload and retry") from None
    except ValueError:
        raise HTTPException(400, "Invalid configuration") from None
    finally:
        c.close()
    return redirect("/settings")


@router.post("/settings/restore/{revision_id}")
def restore_profile(request: Request, revision_id: str, expected: str = Form("")):
    c = db.connect()
    try:
        uid = reader(request, c)
        row = c.execute("SELECT * FROM profile_revisions WHERE user_id=? AND id=?", (uid, revision_id)).fetchone()
        if not row:
            raise HTTPException(404)
        body = profiles._path(uid, row["kind"], row["id"]).read_text(encoding="utf-8")
        profiles.save(
            c,
            uid,
            row["kind"],
            body,
            expected or None,
            terms=json.loads(row["search_terms"]),
            origin="restore:" + revision_id,
        )
    except profiles.RevisionConflict:
        raise HTTPException(409, "Configuration changed; reload and retry") from None
    finally:
        c.close()
    return redirect("/settings")


@router.post("/settings/delivery")
def delivery(
    request: Request,
    daily_limit: int = Form(..., ge=1, le=50),
    timezone: str = Form(..., max_length=100),
    blocked_authors: str = Form("", max_length=8000),
):
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(400, "Invalid timezone") from None
    blocks = [v.strip() for v in blocked_authors.splitlines() if v.strip()]
    if any(not v.startswith(("bluesky:", "x:")) for v in blocks):
        raise HTTPException(400, "Use platform-qualified author IDs")
    c = db.connect()
    try:
        c.execute(
            """UPDATE reader_settings SET daily_limit=?,timezone=?,blocked_authors=?,version=version+1,
            refresh_requested=1 WHERE user_id=?""",
            (daily_limit, timezone, json.dumps(blocks), reader(request, c)),
        )
    finally:
        c.close()
    return redirect("/settings")


@router.post("/settings/notes")
def add_note(request: Request, title: str = Form(..., max_length=200), text: str = Form(..., max_length=200000)):
    c = db.connect()
    try:
        notes.import_note(c, reader(request, c), title, text)
    except ValueError:
        raise HTTPException(400, "Invalid note") from None
    finally:
        c.close()
    return redirect("/settings")


@router.post("/settings/notes/{note_id}/delete")
def remove_note(request: Request, note_id: str):
    c = db.connect()
    try:
        uid = reader(request, c)
        if not c.execute("SELECT 1 FROM note_versions WHERE user_id=? AND id=?", (uid, note_id)).fetchone():
            raise HTTPException(404)
        notes.delete_note(c, uid, note_id)
    finally:
        c.close()
    return redirect("/settings")
