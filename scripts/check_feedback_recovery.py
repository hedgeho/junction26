"""Browser check of real feedback API receipt recovery in an isolated source snapshot.

Run like check_reader_ui.py; all configuration writes stay in a temporary DB.
"""

import asyncio
import contextlib
import json
import sys
from unittest.mock import patch

import check_reader_ui as audit
from curator_core import db
from curator_curation import jobs
from curator_curation.llm.schemas import ProfileProposal
from playwright.async_api import async_playwright, expect


class Model:
    def update_profile(self, current, feedback, terms=()):
        return ProfileProposal(
            markdown=current + "\n- [rule] Prefer thoughtful writing with concrete evidence\n",
            search_terms=list(terms),
        )


def apply_feedback():
    with contextlib.closing(db.connect()) as conn:
        assert jobs.process_one(conn, Model(), include_notes=False)


def job_count():
    with contextlib.closing(db.connect()) as conn:
        return conn.execute("SELECT count(*) FROM feedback_jobs WHERE source_chunk_id IS NULL").fetchone()[0]


async def check():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="msedge" if sys.platform == "win32" else None, headless=True)
        context = await browser.new_context(viewport={"width": 1440, "height": 1000})
        page = await context.new_page()
        errors, lookups = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.goto(audit.ORIGIN + "/__audit/sign-in")
        await expect(page.locator(".post")).to_have_count(10)

        async def missing_job(route):
            lookups.append(route.request.url)
            await route.fulfill(status=404, json={"detail": "Not Found"})

        await page.route("**/api/jobs/*", missing_job)
        await page.locator(".post").first.locator(".feedback-open").click()
        await page.locator("#feedback").fill("More thoughtful writing with concrete evidence")
        async with page.expect_response(
            lambda response: response.url.endswith("/me/feedback") and response.request.method == "POST"
        ) as submitted:
            await page.locator("#feedback").press("Control+Enter")
        response = await submitted.value
        assert response.status == 202
        for _ in range(60):
            if lookups:
                break
            await asyncio.sleep(0.1)
        assert lookups, "The failed primary lookup must actually be exercised"
        await page.reload()
        await page.locator(".post").first.locator(".feedback-open").click()
        await asyncio.to_thread(apply_feedback)
        await expect(page.locator("#feedback-panel .feedback-receipt")).to_be_visible(timeout=15000)
        await expect(page.locator("#feedback-panel .feedback-summary")).to_contain_text("thoughtful writing")
        assert await page.locator("#feedback-panel .diff-add").count() > 0
        assert "no longer available" not in await page.locator("#feedback-panel").inner_text()
        await page.screenshot(path=str(audit.OUT / "feedback-recovered-receipt.png"))
        await page.locator("#feedback-close").click()
        await page.reload()
        await page.locator(".post").first.locator(".feedback-open").click()
        await expect(page.locator("#feedback-panel .feedback-receipt")).to_be_visible()
        await page.locator("#feedback-close").click()

        async def missing_key(route):
            await route.fulfill(status=404, json={"detail": "Not Found"})

        await page.route("**/api/feedback-status?*", missing_key)
        await page.locator(".post").nth(1).locator(".feedback-open").click()
        text = "More careful source-based discussion"
        await page.locator("#feedback").fill(text)
        await page.locator("#feedback").press("Control+Enter")
        await expect(page.locator("#feedback-panel .feedback-progress")).to_contain_text(
            "Your text is saved", timeout=20000
        )
        await expect(page.locator("#feedback")).to_have_value(text)
        await expect(page.locator("#feedback-panel button[type=submit]")).to_be_enabled()
        before = await asyncio.to_thread(job_count)
        await page.unroute("**/api/feedback-status?*", missing_key)
        await page.locator("#feedback").press("Control+Enter")
        await asyncio.sleep(0.5)
        assert await asyncio.to_thread(job_count) == before, "Retry must reuse the accepted submission key"
        await asyncio.to_thread(apply_feedback)
        await expect(page.locator("#feedback-panel .feedback-receipt")).to_be_visible(timeout=15000)
        assert not errors, errors
        print(
            json.dumps(
                {
                    "passed": [
                        "real POST accepts owned post feedback",
                        "404 primary lookup recovers real stored job by submission key",
                        "applied feedback displays its own literal summary and diff",
                        "pending feedback recovers across reload and receipt persists",
                        "missing recovery preserves text and enables retry",
                        "retry does not create a duplicate job",
                        "zero browser exceptions",
                    ]
                },
                indent=2,
            )
        )
        await browser.close()


async def configured_check():
    from curator_web import site

    config = site.curation_settings().model_copy(
        update={"gemma_url": "http://127.0.0.1:1", "gemma_token": "audit-only"}
    )
    with patch.object(site, "curation_settings", return_value=config):
        await check()


if __name__ == "__main__":
    audit.main = configured_check
    asyncio.run(audit.run_isolated())
