"""Run browser regressions against an isolated copy of real local data.

From the repository root:
    uv run --with playwright python scripts/check_reader_ui.py

Requires Chromium (or Microsoft Edge on Windows) and a local database with at
least ten available real posts. Every state-changing action uses a temporary
SQLite copy or intercepted test response. Bluesky writes are blocked by the
local test server as well as intercepted by the browser. No login bypass or
test endpoint is added to the production application.
"""

import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.async_api import async_playwright, expect

ORIGIN = ""
OUT = Path(__file__).resolve().parents[1] / "data" / "ui-audit"
OUT.mkdir(parents=True, exist_ok=True)
checks = []


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="msedge" if sys.platform == "win32" else None, headless=True)
        context = await browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
        page = await context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.goto(ORIGIN + "/__audit/sign-in")
        await expect(page.locator(".post")).to_have_count(10)
        originals = await page.locator(".post-text").all_text_contents()
        keys = await page.locator(".post").evaluate_all("(nodes)=>nodes.map(n=>n.dataset.postKey)")
        metadata = await page.evaluate('JSON.parse(document.getElementById("post-metadata").textContent)')
        assert set(metadata) == set(keys)
        checks.append("real source posts, hard cap and source metadata bootstrap")
        await page.locator("#daily-limit-open").click()
        await expect(page.locator("#daily-limit-dialog")).to_be_visible()
        await page.locator("#feed-daily-limit").fill("0")
        await page.locator("#feed-daily-limit").press("Control+Enter")
        assert await page.locator("#feed-daily-limit").evaluate("(n)=>!n.validity.valid")
        await page.locator("#feed-daily-limit").fill("3")
        await page.locator("#feed-daily-limit").press("Control+Enter")
        await expect(page.locator(".post")).to_have_count(3)
        await page.locator("#daily-limit-open").click()
        await page.locator("#feed-daily-limit").fill("10")
        await page.locator("#feed-daily-limit").press("Control+Enter")
        await expect(page.locator(".post")).to_have_count(10)
        checks.append("daily limit validation, keyboard submission and immediate deterministic cut")

        posts = page.locator(".post")
        await expect(posts.first.locator(".social-interact[data-action=like]")).to_be_enabled()
        await posts.first.locator(".feedback-open").click()
        await page.locator("#feedback").fill("Draft A")
        await page.locator("#feedback-close").click()
        await posts.nth(1).locator(".feedback-open").click()
        await page.locator("#feedback").fill("Draft B")
        await page.locator("#feedback-close").click()
        await posts.first.locator(".feedback-open").click()
        await expect(page.locator("#feedback")).to_have_value("Draft A")
        await page.locator("#feedback-close").click()
        checks.append("per-post drafts and panel focus return")

        jobs = {}
        submissions = []

        async def submit_feedback(route):
            body = route.request.post_data
            job = "job" + str(len(submissions))
            submissions.append({"body": body, "url": route.request.url, "job": job})
            jobs[job] = {"state": "queued"}
            await asyncio.sleep(0.15)
            await route.fulfill(status=202, json={"job_id": job, "next_key": "next-" + job})

        async def poll_job(route):
            await route.fulfill(json=jobs[urlsplit(route.request.url).path.rsplit("/", 1)[-1]])

        await page.route("**/me/feedback", submit_feedback)
        await page.route("**/me/more", submit_feedback)
        await page.route("**/api/jobs/*", poll_job)
        await posts.first.locator(".feedback-open").click()
        await page.locator("#feedback").press("Control+Enter")
        await page.locator("#feedback").press("Control+Enter")
        await page.locator("#feedback-close").click()
        await posts.nth(1).locator(".feedback-open").click()
        await expect(page.locator("#feedback")).to_have_value("Draft B")
        await asyncio.sleep(0.25)
        assert len(submissions) == 1
        jobs["job0"] = {
            "state": "applied",
            "changes": ["Saved preference A"],
            "job_id": "job0",
            "feedback": "Draft A",
            "post_key": keys[0],
            "diff": {
                "filename": "feed-curation.md",
                "title": "Feed curation",
                "changed": True,
                "added": 1,
                "removed": 1,
                "format_changes": [],
                "unavailable": False,
                "lines": [
                    {"type": "file", "text": "--- feed-curation.md (before)"},
                    {"type": "file", "text": "+++ feed-curation.md (after)"},
                    {"type": "hunk", "text": "@@ -1 +1 @@"},
                    {"type": "remove", "text": "-Old preference"},
                    {"type": "add", "text": "+New preference <script>window.diffExecuted=1</script>"},
                ],
            },
        }
        await asyncio.sleep(1.7)
        await expect(page.locator("#feedback")).to_have_value("Draft B")
        await expect(page.locator("#feedback-panel .feedback-progress")).to_have_text("")
        await expect(page.locator("#feedback-panel .feedback-receipt")).to_have_count(0)
        await page.locator("#feedback-close").click()
        await posts.first.locator(".feedback-open").click()
        await expect(page.locator("#feedback-panel .feedback-progress")).to_have_text("Saved preference A")
        await expect(page.locator("#feedback")).to_have_value("")
        await expect(page.locator("#feedback-panel .feedback-request")).to_have_text("Draft A")
        await expect(page.locator("#feedback-panel .diff-remove")).to_have_text("-Old preference")
        await expect(page.locator("#feedback-panel .diff-add")).to_have_text(
            "+New preference <script>window.diffExecuted=1</script>"
        )
        assert not await page.evaluate("window.diffExecuted || false")
        await expect(page.locator("#feedback-panel .feedback-receipt a")).to_have_attribute(
            "href", "/settings/history/feed-curation"
        )
        await page.screenshot(path=str(OUT / "feedback-diff-desktop.png"))
        await page.set_viewport_size({"width": 320, "height": 740})
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        await page.screenshot(path=str(OUT / "feedback-diff-mobile.png"))
        await page.set_viewport_size({"width": 1440, "height": 1000})
        assert keys[0] in submissions[0]["body"]
        checks.append("feedback races, correct source context, duplicate protection and scoped completion")
        checks.append("exact inline feedback diff, source association, safe literal rendering and narrow layout")

        await page.locator("#feedback").fill("Try this again")
        await page.locator("#feedback").press("Control+Enter")
        await page.locator("#feedback-close").click()
        await posts.nth(1).locator(".feedback-open").click()
        await asyncio.sleep(0.25)
        jobs["job1"] = {"state": "failed"}
        await asyncio.sleep(1.7)
        await expect(page.locator("#feedback")).to_have_value("Draft B")
        await page.locator("#feedback-close").click()
        await posts.first.locator(".feedback-open").click()
        await expect(page.locator("#feedback")).to_have_value("Try this again")
        await expect(page.locator("#feedback-panel .feedback-progress")).to_contain_text("try again")
        await page.locator("#feedback").fill("")
        await page.locator("#feedback-close").click()
        await posts.nth(1).locator(".feedback-open").click()
        await page.locator("#feedback").fill("")
        await page.locator("#feedback-close").click()
        checks.append("failed feedback restored to its own post without overwriting another draft")

        await page.locator("#more-feedback").fill("More like these")
        await page.locator("#more-feedback").press("Control+Enter")
        await asyncio.sleep(0.25)
        jobs["job2"] = {
            "state": "applied",
            "changes": ["Saved a lasting preference"],
            "job_id": "job2",
            "feedback": "More like these",
            "post_key": "",
            "diff": {
                "filename": "feed-curation.md",
                "title": "Feed curation",
                "changed": False,
                "added": 0,
                "removed": 0,
                "lines": [],
                "format_changes": [],
                "unavailable": False,
            },
        }
        await expect(page.locator(".more-section .feedback-progress")).to_have_text(
            "Saved a lasting preference", timeout=5000
        )
        assert submissions[2]["url"].endswith("/me/feedback") and keys[0] in submissions[2]["body"]
        await expect(page.locator(".more-section .feedback-receipt .diff-note")).to_have_text(
            "No configuration changes."
        )
        await expect(page.locator(".more-section .feedback-request")).to_have_text("More like these")
        await posts.first.locator(".social-interact[data-action=comment]").click()
        await page.locator("#social-text").fill("Unsent comment draft")
        await page.locator("#social-close").click()
        await page.route("**/api/curation", lambda route: route.fulfill(json={"state": "ready", "label": "Ready"}))
        await page.locator("#more-feedback").fill("Only compilers now")
        await page.locator(".more-action-primary").focus()
        await page.locator(".more-action-primary").press("Control+Enter")
        await asyncio.sleep(0.25)
        jobs["job3"] = {"state": "applied", "changes": ["Added a tentative interest"]}
        await expect(page.locator("#feed-ready-message")).to_contain_text("Finish or clear your draft", timeout=6500)
        assert submissions[3]["url"].endswith("/me/more")
        await posts.first.locator(".social-interact[data-action=comment]").click()
        await expect(page.locator("#social-text")).to_have_value("Unsent comment draft")
        await page.locator("#social-text").fill("")
        async with page.expect_navigation(wait_until="domcontentloaded"):
            await page.locator("#social-close").click()
        await page.unroute("**/api/curation")
        await expect(page.locator("#feed-ready-message")).to_be_hidden()
        checks.append(
            "both intentional feed actions, keyboard choice, full-feed context and reload protection for social drafts"
        )
        print("Feedback and keyboard checks passed", flush=True)

        # Responses below are isolated UI simulations; no social API writes reach Bluesky.
        social = []
        state = {"like": False, "save": False, "repost": False}
        reply_uri = "at://did:plc:audit/app.bsky.feed.post/reply"

        async def social_action(route):
            body = route.request.post_data
            action = next(
                action
                for action in ["comment", "quote", "like", "save", "repost"]
                if "\r\n\r\n" + action + "\r\n" in body
            )
            social.append(action)
            if action in state:
                state[action] = not state[action]
                result = {"active": state[action]}
            else:
                result = {"active": True, "record_uri": reply_uri if action == "comment" else reply_uri + "quote"}
            await asyncio.sleep(0.15)
            await route.fulfill(json=result)

        deleted = []

        async def delete(route):
            deleted.append(route.request.post_data)
            await route.fulfill(json={"deleted": True})

        async def comments(route):
            parent = parse_qs(urlsplit(route.request.url).query).get("parent_uri", [""])[0]
            tail = parent.rsplit("/", 1)[-1]
            depth = int(tail.removeprefix("nested-")) + 1 if tail.startswith("nested-") else 1
            items = [
                {
                    "uri": reply_uri + str(i) if not parent else reply_uri.rsplit("/", 1)[0] + "/nested-" + str(depth),
                    "text": "Thread reply " + str(i),
                    "author": "Reader " + str(i),
                    "handle": "reader.test",
                    "is_self": False,
                    "reply_count": 1 if i == 0 and (not parent or depth < 6) else 0,
                    "state": {"like": False, "save": False, "repost": False},
                }
                for i in range(5 if not parent else 1)
            ]
            await route.fulfill(json={"items": items, "total": len(items)})

        await page.route("**/api/social-action", social_action)
        await page.route("**/api/comment/delete", delete)
        await page.route("**/api/comments?*", comments)
        await page.reload()
        await expect(posts.first.locator(".social-interact[data-action=like]")).to_be_enabled()
        for action in ["like", "save"]:
            button = posts.first.locator(".social-interact[data-action=" + action + "]")
            await button.click()
            await expect(button).to_have_attribute("aria-pressed", "true")
            await button.click()
            await expect(button).to_have_attribute("aria-pressed", "false")
        await posts.first.locator(".repost-menu-open").click()
        await posts.first.locator("[data-action=repost]").click()
        await expect(posts.first.locator(".direct-repost")).to_be_visible()
        await posts.first.locator(".repost-menu-open").click()
        await posts.first.locator("[data-action=repost]").click()
        await expect(posts.first.locator(".direct-repost")).to_have_count(0)
        checks.append("like/save filled state, undo and in-app direct repost outline")
        await page.evaluate("""() => {
            Object.defineProperty(navigator, 'share', {value:undefined, configurable:true});
            Object.defineProperty(navigator, 'clipboard', {value:{writeText:async value => {window.auditCopiedLink=value;}}, configurable:true});
        }""")
        await posts.first.locator(".share-post").click()
        assert await page.evaluate("window.auditCopiedLink") == await posts.first.get_attribute("data-source-url")
        assert page.url == ORIGIN + "/me"
        checks.append("sharing copies the canonical source link without navigating away")

        await posts.first.locator(".repost-menu-open").click()
        await posts.first.locator("[data-action=quote]").click()
        await expect(page.locator("#social-context-text")).to_have_text(originals[0])
        await page.locator("#social-text").fill("My exact quote comment")
        await page.locator("#social-text").press("Control+Enter")
        await page.locator("#social-text").press("Control+Enter")
        await expect(page.locator("#social-dialog")).not_to_be_visible()
        assert social.count("quote") == 1
        await expect(posts.first.locator(".contribution")).to_contain_text("My exact quote comment")
        page.once("dialog", lambda dialog: dialog.accept())
        await posts.first.locator(".comment-delete").click()
        await expect(posts.first.locator(".contribution")).to_have_count(0)
        assert "quote" in deleted[-1]
        checks.append("quote context, keyboard submit, duplicate protection, outline and verified deletion UI")

        await posts.first.locator(".social-interact[data-action=comment]").click()
        await page.locator("#social-text").fill("My exact reply")
        await page.locator("#social-text").press("Control+Enter")
        await expect(page.locator("#social-dialog")).not_to_be_visible()
        await expect(posts.first.locator(".comments-list")).to_be_visible()
        await expect(posts.first.locator(".comments-list > .thread-item")).to_have_count(3)
        await posts.first.locator(".comments-list > .thread-more").click()
        await expect(posts.first.locator(".comments-list > .thread-item")).to_have_count(6)
        await expect(posts.first.locator(".comment-row").filter(has_text="My exact reply")).to_have_count(1)
        own = posts.first.locator(".comment-row").filter(has_text="My exact reply")
        await expect(own.locator(".feedback-open")).to_have_count(0)
        assert await own.locator(".comment-actions button:visible").count() == 5
        page.once("dialog", lambda dialog: dialog.accept())
        await own.locator(".comment-delete").click()
        await expect(own).to_have_count(0)
        branch = posts.first.locator(".comments-list > .thread-item").first
        await branch.locator(".comment-body > .thread-more").click()
        await expect(branch.locator(".thread-children .comment-row")).to_have_count(1)
        await branch.locator(".thread-children .comment-actions button").first.click()
        await expect(page.locator("#social-context-text")).to_have_text("Thread reply 0")
        await page.locator("#social-close").click()
        checks.append(
            "own reply appears once, deletes, replies page incrementally, nested replies and reply interactions"
        )
        node = branch.locator(":scope > .comment-row > .comment-body > .thread-children > .thread-item").first
        for _ in range(5):
            await node.locator(":scope > .comment-row > .comment-body > .thread-more").click()
            node = node.locator(":scope > .comment-row > .comment-body > .thread-children > .thread-item").first
            await expect(node).to_be_visible()
        await page.set_viewport_size({"width": 320, "height": 740})
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        await page.set_viewport_size({"width": 1440, "height": 1000})
        checks.append("six-level reply chains remain usable at 320px without horizontal overflow")

        # Explicit failure responses stay readable and preserve the text.
        await page.unroute("**/api/social-action", social_action)
        await page.route(
            "**/api/social-action",
            lambda route: route.fulfill(status=502, json={"detail": "Bluesky is unavailable. Try again later."}),
        )
        await posts.first.locator(".social-interact[data-action=comment]").click()
        await page.locator("#social-text").fill("Keep this on failure")
        await page.locator("#social-text").press("Control+Enter")
        await expect(page.locator(".social-dialog-status")).to_have_text("Bluesky is unavailable. Try again later.")
        await expect(page.locator("#social-text")).to_have_value("Keep this on failure")
        await expect(page.locator("#social-form button[type=submit]")).to_be_enabled()
        await page.locator("#social-text").fill("")
        await page.locator("#social-close").click()
        checks.append("provider failure keeps composer text and restores controls")

        # Streamed note matches must never hold up the feed, and can change after a ready batch.
        raw = await (await page.request.get(ORIGIN + "/me")).text()
        note_html = raw.replace('class="note-matches"', 'class="note-matches" data-live-notes')
        await page.route("**/me?audit-notes=1", lambda route: route.fulfill(content_type="text/html", body=note_html))
        note_state = {"state": "processing", "items": {keys[0]: []}}
        await page.route("**/api/note-matches", lambda route: route.fulfill(json=note_state))
        await page.clock.install()
        await page.goto(ORIGIN + "/me?audit-notes=1")
        await expect(posts).to_have_count(10)
        await expect(page.locator(".note-match")).to_have_count(0)
        note_state = {
            "state": "ready",
            "items": {
                keys[0]: [
                    {
                        "chunk_id": "owned-test-excerpt",
                        "title": "A test note",
                        "text": "Exact original excerpt.\n<script>not executable</script>",
                    }
                ]
            },
        }
        await page.clock.fast_forward(1600)
        await expect(posts.first.locator(".note-match")).to_have_count(1)
        await expect(posts.first.locator(".note-match blockquote")).to_have_text(
            "Exact original excerpt.\n<script>not executable</script>"
        )
        assert await posts.first.locator(".note-match script").count() == 0
        note_state = {"state": "ready", "items": {keys[0]: []}}
        await page.clock.fast_forward(31000)
        await expect(posts.first.locator(".note-match")).to_have_count(0)
        await page.clock.resume()
        checks.append("note matches stream after the feed, stay source-only and refresh after completion")

        await page.goto(ORIGIN + "/settings#configuration")
        assert await page.locator("#configuration").evaluate("(n)=>n.open")
        await expect(page.locator(".connection-top").filter(has_text="Threads")).to_contain_text("Coming soon")
        await page.locator("input[name=enabled]").check()
        await page.locator("#folder").fill(
            "https://drive.google.com/drive/folders/first\nhttps://drive.google.com/drive/folders/second"
        )
        await expect(page.locator("#folder")).to_have_value(
            "https://drive.google.com/drive/folders/first\nhttps://drive.google.com/drive/folders/second"
        )
        assert (
            await page.locator("#notes-help").inner_text()
            == "One link per line. Agora reads Markdown files without changing them."
        )
        await expect(page.get_by_role("link", name="Context.dev privacy policy")).to_have_attribute(
            "href", "https://context.dev/privacy"
        )
        checks.append(
            "settings, configuration discovery, multiple note links, processing disclosure and Threads coming soon"
        )
        config = page.locator("#config-feed-curation")
        initial = "# Feed curation\n\n- [topic] Initial audit preference\n"
        changed = "# Feed curation\n\n- [topic] Changed audit preference\n"
        await config.fill(initial)
        async with page.expect_navigation():
            await config.press("Control+Enter")
        original_download = await page.request.get(ORIGIN + "/settings/profile/feed-curation")
        original_bytes = await original_download.body()
        await config.fill(changed)
        async with page.expect_navigation():
            await config.press("Control+Enter")
        await page.locator(".config-doc").first.locator(".revision-history > summary").click()
        old = (
            page.locator(".revision-entry")
            .filter(has_text="Initial audit preference")
            .filter(has=page.get_by_role("button", name="Restore this version"))
            .first
        )
        async with page.expect_navigation():
            await old.get_by_role("button", name="Restore this version").click()
        await expect(config).to_have_value(initial)
        download = await page.request.get(ORIGIN + "/settings/profile/feed-curation")
        assert await download.body() == original_bytes
        await page.locator(".config-doc").first.locator(".revision-history > summary").click()
        await page.locator(".config-doc").first.get_by_role("link", name="Browse all versions").click()
        await expect(page.locator(".configuration-history")).to_be_visible()
        await page.locator(".revision-diff > summary").first.click()
        await expect(page.locator(".revision-entry").first.locator(".configuration-diff")).to_be_visible()
        assert await page.locator(".revision-entry").first.locator(".diff-add").count() > 0
        assert await page.locator(".revision-entry").first.locator(".diff-remove").count() > 0
        await page.screenshot(path=str(OUT / "configuration-diff-history.png"))
        await page.get_by_role("link", name="Configuration files", exact=False).click()
        checks.append("full configuration revision diffs and complete history navigation")
        checks.append("configuration edit, literal revision history, restore and exact Markdown download")
        for width, height in [(1440, 1000), (768, 1024), (390, 844), (320, 740)]:
            await page.set_viewport_size({"width": width, "height": height})
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            await page.goto(ORIGIN + "/me")
            await expect(page.locator(".post")).to_have_count(10)
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            await posts.first.locator(".feedback-open").click()
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            await page.locator("#feedback").fill("A minimal, focused feed of thoughtful conversations.")
            if width == 390:
                await page.screenshot(path=str(OUT / "after-feedback-mobile.png"))
            await page.locator("#feedback").fill("")
            await page.locator("#feedback-close").click()
            if width == 390:
                await page.screenshot(path=str(OUT / "after-mobile.png"))
            await page.goto(ORIGIN + "/settings")
        checks.append("desktop, tablet, phone and 320px layouts without horizontal overflow")
        await page.set_viewport_size({"width": 1440, "height": 1000})
        await page.goto(ORIGIN + "/me")
        await page.wait_for_timeout(1500)
        images = page.locator(".post-images a")
        if await images.count():
            await images.first.click()
            await expect(page.locator("#image-dialog")).to_be_visible()
            await page.keyboard.press("Escape")
            await expect(page.locator("#image-dialog")).not_to_be_visible()
            checks.append("real Bluesky image opens in-place and Escape returns to the feed")
        await page.locator("#more-feedback").scroll_into_view_if_needed()
        await page.screenshot(path=str(OUT / "after-composer-desktop.png"))
        await page.evaluate("scrollTo(0,0)")
        await page.screenshot(path=str(OUT / "after-desktop.png"))
        await page.get_by_role("button", name="Switch to dark theme").click()
        assert await page.evaluate("document.documentElement.dataset.theme") == "dark"
        await page.reload()
        assert await page.evaluate("document.documentElement.dataset.theme") == "dark"
        await page.screenshot(path=str(OUT / "after-dark.png"))
        checks.append("theme switching and persistence")
        assert not errors, errors
        checks.append("zero uncaught browser errors across all checks")
        (OUT / "browser-report.json").write_text(
            json.dumps({"checks": checks, "errors": errors}, indent=2), encoding="utf-8"
        )
        print(json.dumps({"passed": len(checks), "checks": checks, "errors": errors}, indent=2))
        await browser.close()


async def run_isolated():
    import contextlib
    import os
    import secrets
    import shutil
    import socket
    import sqlite3
    import tempfile

    import uvicorn
    from curator_core import db
    from curator_core.config import CoreSettings, clear_settings
    from curator_curation import digests, profiles
    from curator_web.app import create_app
    from fastapi import Request
    from fastapi.responses import JSONResponse, RedirectResponse

    global ORIGIN
    source_dir = CoreSettings().data_dir.resolve()
    if not (source_dir / "curator.db").is_file():
        raise SystemExit("A local Agora database with real feed data is required.")
    with tempfile.TemporaryDirectory(prefix="agora-browser-audit-") as directory:
        state = Path(directory)
        with (
            contextlib.closing(
                sqlite3.connect(f"file:{source_dir.as_posix()}/curator.db?mode=ro", uri=True)
            ) as original,
            contextlib.closing(sqlite3.connect(state / "curator.db")) as copied,
        ):
            original.backup(copied)
        if (source_dir / "profiles").is_dir():
            shutil.copytree(source_dir / "profiles", state / "profiles")
        os.environ.update(
            DATA_DIR=str(state),
            HOSTNAME="feed.localhost",
            FOR_YOU_SYNC_INTERVAL_S="0",
            SESSION_SECRET=secrets.token_hex(32),
            GEMINI_API_KEY="",
            GEMMA_URL="",
            GEMMA_TOKEN="",
        )
        clear_settings()
        with contextlib.closing(db.connect()) as connection:
            account = connection.execute("SELECT bluesky_did FROM reader_accounts LIMIT 1").fetchone()
            if not account:
                raise SystemExit("A signed-in account with real feed data is required.")
            did = account[0]
            user_id = profiles.bootstrap(connection, did)
            db.kv_set(connection, "setup:" + user_id, "done")
            connection.execute("UPDATE reader_settings SET daily_limit=10 WHERE user_id=?", (user_id,))
            connection.execute("DELETE FROM feed_requests")
            connection.execute("DELETE FROM feedback_jobs")
            if len(digests.read(connection, user_id)) < 10:
                raise SystemExit("At least ten available real posts are required; refresh local ingestion first.")
        app = create_app()

        @app.get("/__audit/sign-in")
        def sign_in(request: Request):
            request.session.update(did=did, csrf="browser-audit")
            return RedirectResponse("/me")

        @app.middleware("http")
        async def block_social_writes(request, call_next):
            if request.url.path in (
                "/api/social-action",
                "/api/comment/delete",
                "/login",
                "/settings/bluesky/connect",
                "/settings/bluesky/sync",
            ):
                return JSONResponse({"detail": "Remote writes are blocked during browser tests."}, status_code=409)
            if request.url.path == "/api/social-state":
                with contextlib.closing(db.connect()) as connection:
                    return JSONResponse(
                        {
                            "items": {
                                post["key"].removeprefix("bluesky:"): {"like": False, "repost": False, "save": False}
                                for post in digests.read(connection, user_id)
                            },
                            "contributions": {},
                        }
                    )
            return await call_next(request)

        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            ORIGIN = "http://127.0.0.1:" + str(listener.getsockname()[1])
            server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
            task = asyncio.create_task(server.serve(sockets=[listener]))
            while not server.started:
                if task.done():
                    await task
                await asyncio.sleep(0.02)
            try:
                await main()
            finally:
                server.should_exit = True
                await task


if __name__ == "__main__":
    asyncio.run(run_isolated())
