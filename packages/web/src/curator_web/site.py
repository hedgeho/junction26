"""Source-only reader, quiet feedback jobs, and opt-in configuration access."""

import asyncio
import hmac
import json
import logging
import secrets
import time
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from curator_core import db
from curator_curation import digests, drive_notes, health, jobs, note_folders, profiles
from curator_curation.config import settings as curation_settings
from curator_curation.readers import ensure_user
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from . import appview, bluesky_feed, oauth, post_details, social

log = logging.getLogger(__name__)


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
    static = Path(__file__).parent / "static"
    version = max((static / name).stat().st_mtime_ns for name in ("app.css", "app.js"))
    return templates.TemplateResponse(request, name, dict(context, csrf=token, asset_version=version))


def current_did(request: Request) -> str:
    did = request.session.get("did")
    if not did:
        raise HTTPException(303, headers={"Location": "/"})
    return did


def reader(request: Request, conn) -> str:
    did = current_did(request)
    ensure_user(conn, did)
    return profiles.bootstrap(conn, did)


def reader_posts(conn, uid, did):
    if not note_folders.setup_done(conn, uid):
        return []
    return digests.read(conn, uid)


def redirect(path="/me"):
    return RedirectResponse(path, status_code=303)


@router.get("/oauth-client-metadata.json")
def oauth_client_metadata():
    return oauth.client().client_metadata


@router.get("/oauth/jwks.json")
def oauth_jwks():
    return oauth.client().jwks


@router.post("/login")
async def login(request: Request, handle: str = Form(..., min_length=1, max_length=253)):
    try:
        url = await oauth.client().authorize(handle.strip().lstrip("@"))
    except Exception:  # noqa: BLE001 - sanitize external provider errors at the boundary
        response = page(
            request, "index.html", {"handle": handle, "error": "Could not connect. Check your handle and try again."}
        )
        response.status_code = 400
        return response
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
        uid = profiles.bootstrap(c, did)
        destination = "/me" if note_folders.setup_done(c, uid) else "/setup"
        try:
            handle = (await asyncio.to_thread(appview.get_profile, did)).get("handle")
            c.execute("UPDATE users SET handle=? WHERE did=?", (handle, did))
        except httpx.HTTPError:
            pass
    finally:
        c.close()
    background_sync(did)
    return redirect(destination)


_syncs: set[asyncio.Task] = set()


def background_sync(did: str) -> None:
    """Start or reconnect the reader's For You source without delaying sign-in."""
    task = asyncio.create_task(bluesky_feed.sync(did, force=True, initialize=True))
    _syncs.add(task)
    task.add_done_callback(_syncs.discard)


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
async def me(request: Request):
    c = db.connect()
    try:
        uid = reader(request, c)
        if not note_folders.setup_done(c, uid):
            return redirect("/setup")
        await asyncio.to_thread(note_folders.sync, c, uid)
    finally:
        c.close()
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
                "posts": reader_posts(c, uid, current_did(request)),
                "daily_limit": digests.config_for(c, uid)["daily_limit"],
                "job": dict(pending) if pending else None,
                "curation_status": health.filtering_status(c, uid),
                "submission_key": uuid.uuid4().hex,
                "more_submission_key": uuid.uuid4().hex,
                "feedback_available": bool(curation_settings().gemma_url and curation_settings().gemma_token),
                "gemma_scoring": curation_settings().scoring_backend == "gemma",
            },
        )
    finally:
        c.close()


@router.get("/api/digest")
def digest_api(request: Request):
    c = db.connect()
    try:
        return {"items": reader_posts(c, reader(request, c), current_did(request))}
    finally:
        c.close()


@router.get("/api/post-details")
async def post_details_api(request: Request):
    c = db.connect()
    try:
        posts = reader_posts(c, reader(request, c), current_did(request))
    finally:
        c.close()
    items = {post["key"]: post_details.imported_details(post) for post in posts}
    bluesky = {post["key"].removeprefix("bluesky:"): post for post in posts if post["platform"] == "bluesky"}
    if bluesky:
        try:
            views = await asyncio.to_thread(appview.get_posts, list(bluesky))
        except (httpx.HTTPError, ValueError):
            views = []
        for view in views:
            if not isinstance(view, dict) or view.get("uri") not in bluesky:
                continue
            uri = view["uri"]
            source = post_details.bluesky_details(view, bluesky[uri]["text"])
            for field, value in source.items():
                if value or (field == "reply_count" and value is not None):
                    items["bluesky:" + uri][field] = value
    return {"items": items}


def thread_contains_ancestor(thread: dict, root_uri: str) -> bool:
    """Accept only a visible descendant of a post in this reader's feed."""
    node = thread
    seen = set()
    while isinstance(node, dict):
        view = node.get("post") or {}
        uri = view.get("uri")
        if uri == root_uri:
            return True
        if not isinstance(uri, str) or uri in seen:
            return False
        seen.add(uri)
        node = node.get("parent")
    return False


@router.get("/api/comments")
async def comments_api(request: Request, post_key: str, parent_uri: str = ""):
    did = current_did(request)
    c = db.connect()
    try:
        posts = reader_posts(c, reader(request, c), did)
    finally:
        c.close()
    post = next((p for p in posts if p["key"] == post_key), None)
    if not post or post["platform"] != "bluesky" or not post_key.startswith("bluesky:at://"):
        raise HTTPException(404, "Post unavailable")
    uri = post_key.removeprefix("bluesky:")
    try:
        target = parent_uri or uri
        thread = await asyncio.to_thread(appview.get_thread, target)
        parent = thread.get("post") or {}
        if parent.get("uri") != target or not thread_contains_ancestor(thread, uri):
            raise ValueError("Post unavailable")
        items = []
        for entry in thread.get("replies") or []:
            view = entry.get("post") if isinstance(entry, dict) else None
            if not isinstance(view, dict):
                continue
            record = view.get("record") or {}
            author = view.get("author") or {}
            text = record.get("text")
            if not isinstance(text, str) or not isinstance(view.get("uri"), str):
                continue
            details = post_details.bluesky_details(view, text)
            items.append(
                {
                    "uri": view["uri"],
                    "text": text,
                    "author": details["author_name"] or "Bluesky user",
                    "handle": details["author_handle"],
                    "avatar": details["author_avatar"],
                    "facets": details["facets"],
                    "created_at": record.get("createdAt"),
                    "is_self": author.get("did") == did,
                    "reply_count": details["reply_count"],
                }
            )
        try:
            states = await social.states(did, [item["uri"] for item in items]) if items else {}
        except Exception:  # noqa: BLE001 - comments remain readable if account state is unavailable
            states = {}
        for item in items:
            item["state"] = states.get(item["uri"])
        if items:
            c = db.connect()
            try:
                quote_rows = [
                    dict(row)
                    for row in c.execute(
                        "SELECT source_uri,record_uri FROM social_contributions WHERE user_did=? AND kind='quote' ORDER BY created_at,rowid",
                        (did,),
                    )
                    if row["source_uri"] in {item["uri"] for item in items}
                ]
            finally:
                c.close()
            if quote_rows:
                try:
                    views = await asyncio.to_thread(appview.get_posts, [row["record_uri"] for row in quote_rows])
                except (httpx.HTTPError, ValueError):
                    views = []
                by_uri = {view.get("uri"): view for view in views if isinstance(view, dict)}
                by_source = {item["uri"]: item for item in items}
                for row in quote_rows:
                    view = by_uri.get(row["record_uri"], {})
                    record = view.get("record") or {}
                    subject = ((record.get("embed") or {}).get("record") or {}).get("uri")
                    if (
                        (view.get("author") or {}).get("did") == did
                        and subject == row["source_uri"]
                        and isinstance(record.get("text"), str)
                    ):
                        by_source[subject].setdefault("quotes", []).append(
                            {"uri": row["record_uri"], "text": record["text"]}
                        )
        total = parent.get("replyCount")
        return {"items": items, "total": total if type(total) is int and total >= 0 else None}
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        raise HTTPException(502, "Could not load comments from Bluesky") from None


@router.get("/api/social-state")
async def social_state(request: Request):
    did = current_did(request)
    c = db.connect()
    try:
        posts = reader_posts(c, reader(request, c), did)
        records = [
            dict(row)
            for row in c.execute(
                "SELECT source_uri,record_uri,kind FROM social_contributions WHERE user_did=? ORDER BY created_at,rowid",
                (did,),
            )
        ]
    finally:
        c.close()
    uris = [
        p["key"].removeprefix("bluesky:")
        for p in posts
        if p["platform"] == "bluesky" and p["key"].startswith("bluesky:at://")
    ]
    try:
        items = await social.states(did, uris) if uris else {}
        relevant = [r for r in records if r["source_uri"] in items]
        contributions = {}
        if relevant:
            try:
                views = await asyncio.to_thread(appview.get_posts, [r["record_uri"] for r in relevant])
            except (httpx.HTTPError, ValueError):
                views = []
            by_uri = {v.get("uri"): v for v in views if isinstance(v, dict)}
            for row in relevant:
                view = by_uri.get(row["record_uri"], {})
                record = view.get("record") or {}
                if (view.get("author") or {}).get("did") != did or not isinstance(record.get("text"), str):
                    continue
                subject = (
                    ((record.get("reply") or {}).get("parent") or {}).get("uri")
                    if row["kind"] == "comment"
                    else ((record.get("embed") or {}).get("record") or {}).get("uri")
                )
                if subject != row["source_uri"]:
                    continue
                contributions.setdefault(row["source_uri"], []).append(
                    {"kind": row["kind"], "text": record["text"], "uri": row["record_uri"]}
                )
        return {"items": items, "contributions": contributions}
    except Exception:  # noqa: BLE001 - sanitize OAuth and network errors
        raise HTTPException(403, "Reconnect Bluesky in settings to grant interaction permissions") from None


@router.post("/api/social-action")
async def social_action(
    request: Request,
    post_key: str = Form(..., max_length=1000),
    action: str = Form(..., max_length=20),
    text: str = Form("", max_length=3000),
    target_uri: str = Form("", max_length=1000),
):
    did = current_did(request)
    c = db.connect()
    try:
        posts = reader_posts(c, reader(request, c), did)
        post = next((p for p in posts if p["key"] == post_key), None)
    finally:
        c.close()
    if not post or post["platform"] != "bluesky" or not post_key.startswith("bluesky:at://"):
        raise HTTPException(400, "This action is unavailable for this post")
    try:
        root_uri = post_key.removeprefix("bluesky:")
        target = target_uri or root_uri
        if target != root_uri:
            branch = await asyncio.to_thread(appview.get_thread, target)
            if (branch.get("post") or {}).get("uri") != target or not thread_contains_ancestor(branch, root_uri):
                raise ValueError("Post is not in this thread")
        result = await social.interact(did, target, action, text)
        if action in ("comment", "quote") and result.get("record_uri"):
            try:
                c = db.connect()
                try:
                    c.execute(
                        "INSERT OR IGNORE INTO social_contributions VALUES (?,?,?,?,unixepoch())",
                        (did, target, result["record_uri"], action),
                    )
                finally:
                    c.close()
            except Exception:  # noqa: BLE001 - the post already succeeded; do not invite a retry
                log.warning("Could not save the reference to a completed social post")
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            raise HTTPException(403, "Reconnect Bluesky in settings to grant interaction permissions") from None
        raise HTTPException(502, "Bluesky could not complete this action") from None
    except Exception:  # noqa: BLE001 - do not expose provider credentials
        raise HTTPException(502, "Could not connect to Bluesky. Try reconnecting in settings.") from None


@router.post("/api/comment/delete")
async def delete_comment(
    request: Request,
    post_key: str = Form(..., max_length=1000),
    comment_uri: str = Form(..., max_length=1000),
    parent_uri: str = Form("", max_length=1000),
    kind: str = Form("comment", max_length=20),
):
    if kind not in ("comment", "quote"):
        raise HTTPException(400, "Invalid post type")
    did = current_did(request)
    c = db.connect()
    try:
        posts = reader_posts(c, reader(request, c), did)
    finally:
        c.close()
    if not any(p["key"] == post_key and p["platform"] == "bluesky" for p in posts) or not post_key.startswith(
        "bluesky:at://"
    ):
        raise HTTPException(404, "Post unavailable")
    source_uri = post_key.removeprefix("bluesky:")
    try:
        target_parent = parent_uri or source_uri
        if target_parent != source_uri:
            branch = await asyncio.to_thread(appview.get_thread, target_parent)
            if (branch.get("post") or {}).get("uri") != target_parent or not thread_contains_ancestor(
                branch, source_uri
            ):
                raise ValueError("Comment is not in this thread")
        if kind == "quote":
            await social.delete_quote(did, target_parent, comment_uri)
        else:
            await social.delete_comment(did, target_parent, comment_uri)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            raise HTTPException(403, "Reconnect Bluesky in settings to allow deleting comments") from None
        raise HTTPException(502, "Could not delete this comment on Bluesky") from None
    except Exception:  # noqa: BLE001 - sanitize provider errors
        raise HTTPException(502, "Could not delete this comment on Bluesky") from None
    try:
        c = db.connect()
        try:
            c.execute(
                "DELETE FROM social_contributions WHERE user_did=? AND source_uri=? AND record_uri=? AND kind=?",
                (did, target_parent, comment_uri, kind),
            )
        finally:
            c.close()
    except Exception:  # noqa: BLE001 - the comment was already deleted
        log.warning("Could not remove reference to deleted comment")
    return {"deleted": True}


@router.get("/api/curation")
def curation_status(request: Request):
    c = db.connect()
    try:
        return health.filtering_status(c, reader(request, c))
    finally:
        c.close()


@router.post("/me/feedback")
def feedback(
    request: Request,
    text: str = Form(..., min_length=1, max_length=8000),
    key: str = Form(..., min_length=1, max_length=128),
    kind: str = Form("public"),
    post_key: str = Form("", max_length=1000),
):
    c = db.connect()
    try:
        uid = reader(request, c)
        if post_key and post_key not in {p["key"] for p in digests.read(c, uid)}:
            raise HTTPException(404)
        job_id = jobs.enqueue(c, uid, kind, text, key, post_key or None)
    except ValueError:
        raise HTTPException(400, "Invalid feedback") from None
    finally:
        c.close()
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"job_id": job_id, "next_key": uuid.uuid4().hex}, status_code=202)
    return redirect()


@router.post("/me/more")
def request_more(
    request: Request,
    text: str = Form(..., min_length=1, max_length=8000),
    key: str = Form(..., min_length=1, max_length=128),
):
    c = db.connect()
    try:
        uid = reader(request, c)
        with db.tx(c):
            existing = c.execute(
                "SELECT 1 FROM feedback_jobs WHERE user_id=? AND idempotency_key=?", (uid, key)
            ).fetchone()
            job_id = jobs.enqueue(c, uid, "public", text, key)
            if not existing:
                config = digests.config_for(c, uid)
                day = digests.day_key(config)
                extra = config["requested_more_count"] if config["requested_more_day"] == day else 0
                extra = min(extra + 10, max(0, 200 - config["daily_limit"]))
                c.execute(
                    """UPDATE reader_settings SET requested_more_day=?, requested_more_count=?,
                    refresh_requested=1, version=version+1 WHERE user_id=?""",
                    (day, extra, uid),
                )
    except ValueError:
        raise HTTPException(400, "Invalid request") from None
    finally:
        c.close()
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"job_id": job_id, "next_key": uuid.uuid4().hex}, status_code=202)
    return redirect("/me#more")


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


@router.post("/me/daily-limit")
def set_daily_limit(request: Request, daily_limit: int = Form(..., ge=1, le=50)):
    c = db.connect()
    try:
        uid = reader(request, c)
        c.execute(
            """UPDATE reader_settings SET daily_limit=?, version=version+1, refresh_requested=1
            WHERE user_id=? AND daily_limit<>?""",
            (daily_limit, uid, daily_limit),
        )
    finally:
        c.close()
    return redirect()


@router.post("/me/refresh")
async def refresh(request: Request):
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
def settings_page(request: Request):
    return config_page(request)


def config_page(request: Request, folder_error: str | None = None, submitted_folder: str | None = None):
    c = db.connect()
    try:
        uid = reader(request, c)
        note_folders.sync(c, uid)
        folder = note_folders.folder_for(c, uid)
        docs = [
            {
                "kind": kind,
                "slug": slug,
                "title": title,
                "description": description,
                "revision": (profiles.head(c, uid, kind) or {"id": ""})["id"],
                "body": profiles.markdown(c, uid, kind),
            }
            for kind, slug, title, description in (
                (
                    "public",
                    "feed-curation",
                    "Feed curation",
                    (
                        "Guides which posts are selected for your feed. Your feedback updates this file; "
                        + (
                            "notes can also shape it, and Gemma uses it for filtering."
                            if curation_settings().scoring_backend == "gemma"
                            else "it is sent to Gemini for filtering. Notes do not update it while Gemini filtering is enabled."
                        )
                    ),
                ),
                (
                    "private",
                    "note-matching",
                    "Note matching",
                    "Guides how your notes are matched to curated posts. Notes and this file are processed by Gemma on Modal.",
                ),
            )
        ]
        source_notes = c.execute(
            "SELECT id,title FROM note_versions WHERE user_id=? ORDER BY created_at DESC", (uid,)
        ).fetchall()
        history = c.execute(
            "SELECT id,kind,created_at FROM profile_revisions WHERE user_id=? ORDER BY created_at DESC,rowid DESC LIMIT 20",
            (uid,),
        ).fetchall()
        return page(
            request,
            "settings.html",
            {
                "docs": docs,
                "notes": source_notes,
                "folder": submitted_folder if submitted_folder is not None else note_folders.folder_label(folder),
                "notes_enabled": submitted_folder is not None or bool(folder),
                "drive_viewer": drive_notes.viewer_email(),
                "notes_state": folder["state"] if folder else "",
                "folder_error": folder_error,
                "history": history,
            },
        )
    finally:
        c.close()


@router.get("/settings/profile/{kind}")
def download_profile(request: Request, kind: str):
    kind = {"feed-curation": "public", "note-matching": "private"}.get(kind, kind)
    if kind not in profiles.KINDS:
        raise HTTPException(404)
    filename = {"public": "feed-curation.md", "private": "note-matching.md"}[kind]
    c = db.connect()
    try:
        body = profiles.markdown(c, reader(request, c), kind)
        return PlainTextResponse(
            body,
            headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
        )
    finally:
        c.close()


@router.post("/settings/profile/{kind}")
def edit_profile(request: Request, kind: str, body: str = Form(..., max_length=24000), revision: str = Form("")):
    kind = {"feed-curation": "public", "note-matching": "private"}.get(kind, kind)
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


@router.post("/settings/bluesky/connect")
async def connect_bluesky(request: Request):
    did = current_did(request)
    try:
        url = await oauth.client().authorize(did)
    except Exception:  # noqa: BLE001 - sanitized OAuth boundary
        raise HTTPException(400, "Connection failed; please try again") from None
    return redirect(url)


@router.post("/settings/bluesky/sync")
async def sync_bluesky(request: Request):
    return await refresh(request)


@router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request):
    return setup_view(request)


def setup_view(request: Request, error: str | None = None, status_code: int = 200, submitted_folder: str | None = None):
    c = db.connect()
    try:
        uid = reader(request, c)
        user = c.execute("SELECT handle FROM users WHERE did=?", (current_did(request),)).fetchone()
        folder = note_folders.folder_for(c, uid)
        response = page(
            request,
            "setup.html",
            {
                "did": current_did(request),
                "handle": user["handle"] if user else None,
                "folder": submitted_folder if submitted_folder is not None else note_folders.folder_label(folder),
                "notes_enabled": submitted_folder is not None or bool(folder),
                "drive_viewer": drive_notes.viewer_email(),
                "error": error,
                "curation_status": health.filtering_status(c, uid),
            },
        )
        response.status_code = status_code
        return response
    finally:
        c.close()


@router.post("/setup")
def finish_setup(request: Request, enabled: str = Form(""), folder: str = Form("notes", max_length=2000)):
    c = db.connect()
    error = None
    try:
        uid = reader(request, c)
        try:
            note_folders.configure(c, uid, folder if enabled == "yes" else None)
            db.kv_set(c, "setup:" + uid, "done")
        except ValueError as exc:
            error = str(exc)
    finally:
        c.close()
    if error:
        return setup_view(request, error, 400, folder if enabled == "yes" else None)
    return redirect()


@router.post("/settings/notes/folder")
def set_notes_folder(request: Request, enabled: str = Form(""), folder: str = Form("notes", max_length=2000)):
    c = db.connect()
    error = None
    try:
        note_folders.configure(c, reader(request, c), folder if enabled == "yes" else None)
    except ValueError as exc:
        error = str(exc)
    finally:
        c.close()
    if error:
        response = config_page(request, error, folder if enabled == "yes" else None)
        response.status_code = 400
        return response
    return redirect("/settings#notes")
