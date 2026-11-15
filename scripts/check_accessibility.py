"""Run axe WCAG A/AA checks on isolated real-source reader states.

uv run --with playwright python scripts/check_accessibility.py --axe-script /path/to/axe.min.js

Requires an existing axe-core 4.14.0 browser bundle (for example from the npm
axe-core package). No third-party script is downloaded or added to production.
Uses check_reader_ui's real local source-data requirement and blocked social writes.
Writes data/accessibility.json; detected violations make the command fail.
Automated rules do not replace keyboard, screen-reader or visual review.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import check_reader_ui as audit
from playwright.async_api import async_playwright


async def check():
    report = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="msedge" if sys.platform == "win32" else None, headless=True)
        context = await browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
        page = await context.new_page()
        await page.goto(audit.ORIGIN + "/__audit/sign-in")

        async def scan(name):
            await page.add_script_tag(path=str(AXE_SCRIPT))
            result = await page.evaluate("""async () => {
                const result = await axe.run(document, {runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});
                return {violations:result.violations.map(v=>({id:v.id,impact:v.impact,description:v.description,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary}))})),incomplete:result.incomplete.map(v=>v.id),passed:result.passes.length};
            }""")
            report.append({"page": name, **result})

        for theme in ["light", "dark"]:
            await page.evaluate(
                "theme => {localStorage.setItem('agora-theme',theme);document.documentElement.dataset.theme=theme;}",
                theme,
            )
            for path in ["/me", "/settings", "/settings/history/feed-curation", "/setup"]:
                await page.goto(audit.ORIGIN + path)
                await scan(theme + path)
                if path == "/me":
                    await page.locator(".post .feedback-open").first.click()
                    await scan(theme + "/me feedback panel")
                    await page.screenshot(path=str(audit.OUT / ("audit-feedback-" + theme + ".png")))
                    await page.locator("#feedback-close").click()
                    await page.locator("#daily-limit-open").click()
                    await scan(theme + "/me daily-limit modal")
                    await page.locator("#daily-limit-close").click()
        await page.set_viewport_size({"width": 320, "height": 740})
        await page.goto(audit.ORIGIN + "/me")
        await scan("mobile /me")
        await page.locator(".post .feedback-open").first.click()
        await scan("mobile /me feedback panel")
        await page.locator("#feedback-close").click()
        await page.locator("#daily-limit-open").click()
        await scan("mobile /me daily-limit modal")
        await page.locator("#daily-limit-close").click()
        await page.goto(audit.ORIGIN + "/settings")
        await scan("mobile /settings")
        await context.clear_cookies()
        await page.goto(audit.ORIGIN + "/")
        await scan("landing")
        await page.goto(audit.ORIGIN + "/me")
        assert page.url == audit.ORIGIN + "/"
        await browser.close()
    target = Path(__file__).resolve().parents[1] / "data/accessibility.json"
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    violations = [r for r in report if r["violations"]]
    print(json.dumps({"states": len(report), "violations": violations}, indent=2))
    if violations:
        raise SystemExit("Accessibility violations detected; see data/accessibility.json.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--axe-script", type=Path, default=Path(__file__).resolve().parents[1] / "data/axe.min.js")
    AXE_SCRIPT = parser.parse_args().axe_script.resolve()
    if not AXE_SCRIPT.is_file():
        parser.error("Provide --axe-script pointing to the axe-core browser bundle.")
    audit.main = check
    asyncio.run(audit.run_isolated())
