"""Local UI preview with labeled fixtures; no OAuth, database, or model calls.

Run: uv run python scripts/preview_ui.py
"""

import argparse
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

app = FastAPI(title="Agora local UI preview", docs_url=None, redoc_url=None, openapi_url=None)
templates = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "packages/web/src/curator_web/templates")
# Fictional sample content, labeled in the preview banner. Never imported into live state.
POSTS = [
    {
        "key": "sample:one",
        "platform": "bluesky",
        "author": "maya.research.example",
        "text": "The most interesting question about personal AI isn't what it can do for us. It's what we choose to stop doing.\n\nI'd love to see more tools that give you your afternoon back, instead of finding new ways to fill it.",
        "url": "https://example.com/sample-post-1",
        "notes": [
            {
                "title": "Tools for thinking",
                "text": "A good tool should make itself less necessary over time. The measure is how much room it leaves for a life outside it.",
            }
        ],
    },
    {
        "key": "sample:two",
        "platform": "x",
        "author": "@eli_builds_example",
        "text": "A small experiment: we replaced our product's infinite feed with five things a day.\n\nPeople spent less time in the app. They also came back more intentionally. Maybe time spent was the wrong thing to measure all along.",
        "url": "https://example.com/sample-post-2",
        "notes": [],
    },
    {
        "key": "sample:three",
        "platform": "bluesky",
        "author": "leon.design.example",
        "text": "Reading someone's working notes often teaches me more than reading their finished essay. You can see the questions before they become answers.\n\nWhat would a social space built around unfinished thinking look like?",
        "url": "https://example.com/sample-post-3",
        "notes": [],
    },
]


@app.get("/")
@app.get("/me")
@app.get("/settings")
@app.get("/privacy")
def preview(request: Request):
    pages = {"/": "index.html", "/me": "me.html", "/settings": "settings.html", "/privacy": "privacy.html"}
    return templates.TemplateResponse(
        request,
        pages[request.url.path],
        {
            "demo": True,
            "csrf": "preview-only",
            "submission_key": "preview-only",
            "user": {"handle": "you.example"} if request.url.path != "/" else None,
            "posts": [] if request.query_params.get("empty") == "1" else POSTS,
            "job": None,
            "config": {"daily_limit": 5, "timezone": "Europe/Helsinki", "blocked_authors": ""},
            "docs": [
                {
                    "kind": "public",
                    "revision": "preview",
                    "body": "# Public interests\n\nSample configuration for the local preview.",
                },
                {
                    "kind": "private",
                    "revision": "preview",
                    "body": "# Private interests\n\nSample configuration for the local preview.",
                },
            ],
            "notes": [{"id": "sample", "title": "Tools for thinking"}],
            "history": [],
        },
    )


@app.get("/settings/profile/{kind}")
def preview_download(kind: str):
    return RedirectResponse("/settings")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port)
