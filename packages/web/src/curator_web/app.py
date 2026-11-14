from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import feedgen, oauth, site
from .config import settings


def create_app() -> FastAPI:
    app = FastAPI(title="curator")
    app.add_middleware(SessionMiddleware, secret_key=settings().session_secret, https_only=not oauth.is_dev())
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

    @app.middleware("http")
    async def private_cache_headers(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith(("/me", "/api/", "/settings", "/oauth/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(feedgen.router)
    app.include_router(site.router)
    return app
