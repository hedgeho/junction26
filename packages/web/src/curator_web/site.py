"""Website: sign-in, the reader's description, feedback, and a feed preview."""

import asyncio
import logging
import time
from pathlib import Path

import anthropic
import httpx
from curator_core import db
from curator_curation.config import settings as curation_settings
from curator_curation.llm import profile
from curator_curation.readers import active_preferences, ensure_user
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from . import appview, oauth
from .config import settings

log = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


# ---------------------------------------------------------------- oauth


@router.get("/oauth-client-metadata.json")
def oauth_client_metadata():
    return oauth.client().client_metadata


@router.get("/oauth/jwks.json")
def oauth_jwks():
    return oauth.client().jwks


@router.post("/login")
async def login(handle: str = Form(...)):
    url = await oauth.client().authorize(handle.strip().lstrip("@"))
    return RedirectResponse(url, status_code=303)


@router.get("/oauth/callback")
async def oauth_callback(request: Request):
    result = await oauth.client().callback(dict(request.query_params))
    did = result.session.did
    request.session["did"] = did
    c = db.connect()
    ensure_user(c, did)
    try:
        handle = (await asyncio.to_thread(appview.get_profile, did)).get("handle")
        c.execute("UPDATE users SET handle = ? WHERE did = ?", (handle, did))
    except httpx.HTTPError:
        log.warning("profile lookup failed for %s", did)
    return RedirectResponse("/me", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


# ---------------------------------------------------------------- pages


def current_did(request: Request) -> str:
    did = request.session.get("did")
    if not did:
        raise HTTPException(303, headers={"Location": "/"})
    return did


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    if request.session.get("did"):
        return RedirectResponse("/me", status_code=303)
    return templates.TemplateResponse(request, "index.html", {"feed_uri": settings().feed_uri})


@router.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request):
    return templates.TemplateResponse(request, "privacy.html", {})


@router.get("/me", response_class=HTMLResponse)
async def me(request: Request):
    did = current_did(request)
    c = db.connect()
    user = c.execute("SELECT * FROM users WHERE did = ?", (did,)).fetchone()
    prefs = c.execute("SELECT * FROM preferences WHERE user_did = ? ORDER BY active DESC, id", (did,)).fetchall()
    items = c.execute(
        "SELECT post_uri, reason FROM feed_items WHERE user_did = ? ORDER BY position LIMIT 30", (did,)
    ).fetchall()
    reasons = {r["post_uri"]: r["reason"] for r in items}
    posts = await asyncio.to_thread(appview.get_posts, list(reasons)) if reasons else []
    return templates.TemplateResponse(
        request,
        "me.html",
        {"user": user, "prefs": prefs, "posts": posts, "reasons": reasons, "feed_uri": settings().feed_uri},
    )


async def apply_profile_update(c, did: str, text: str) -> None:
    """Run text through the profile LLM and store the resulting preference changes."""
    current = [dict(p) for p in active_preferences(c, did)]
    client = anthropic.Anthropic(api_key=curation_settings().anthropic_api_key or None)
    update = await asyncio.to_thread(profile.interpret_feedback, client, text, current)
    now = int(time.time())
    with db.tx(c):
        for p in update.add:
            summary = p.summary
            if p.category in ("author_boost", "author_block") and not summary.startswith("did:"):
                try:
                    summary = appview.resolve_handle(summary)
                except httpx.HTTPError:
                    log.warning("could not resolve %r; storing as-is", summary)
            c.execute(
                "INSERT INTO preferences(user_did, category, summary, content, created_at) VALUES (?,?,?,?,?)",
                (did, p.category, summary, p.content, now),
            )
        own = {p["id"] for p in current}
        c.executemany(
            "UPDATE preferences SET active = 0 WHERE id = ? AND user_did = ?",
            [(i, did) for i in update.deactivate_ids if i in own],
        )


@router.post("/me/prompt")
async def save_prompt(request: Request, prompt: str = Form("")):
    did = current_did(request)
    prompt = prompt.strip()[:4000]
    c = db.connect()
    c.execute("UPDATE users SET prompt = ? WHERE did = ?", (prompt, did))
    if prompt:
        await apply_profile_update(c, did, f"The reader set their feed description to: {prompt}")
    return RedirectResponse("/me", status_code=303)


@router.post("/me/feedback")
async def feedback(request: Request, text: str = Form(...)):
    did = current_did(request)
    await apply_profile_update(db.connect(), did, text)
    return RedirectResponse("/me", status_code=303)


@router.post("/me/prefs/{pref_id}/toggle")
def toggle_pref(request: Request, pref_id: int):
    did = current_did(request)
    db.connect().execute("UPDATE preferences SET active = 1 - active WHERE id = ? AND user_did = ?", (pref_id, did))
    return RedirectResponse("/me", status_code=303)
