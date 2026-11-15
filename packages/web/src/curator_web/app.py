import asyncio
import contextlib
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from . import bluesky_feed, feedgen, oauth, site, threads_callbacks
from .config import settings

log = logging.getLogger(__name__)


async def sync_for_you_forever(interval_s: int) -> None:
    while True:
        try:
            await bluesky_feed.sync_all()
        except Exception:  # noqa: BLE001 - keep provider/OAuth error payloads out of logs
            log.warning("For You background sync failed; retrying next interval")
        await asyncio.sleep(interval_s)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    interval = settings().for_you_sync_interval_s
    task = asyncio.create_task(sync_for_you_forever(interval)) if interval > 0 else None
    yield
    if task:
        task.cancel()


def create_app() -> FastAPI:
    app = FastAPI(title="Agora", lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key=settings().session_secret, https_only=not oauth.is_dev())
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

    @app.middleware("http")
    async def private_cache_headers(request, call_next):
        response = await call_next(request)
        if request.url.path in ("/", "/login") or request.url.path.startswith(
            ("/me", "/api/", "/settings", "/setup", "/oauth/")
        ):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(StarletteHTTPException)
    async def browser_error(request: Request, exc: StarletteHTTPException):
        if request.url.path.startswith(("/api/", "/xrpc/", "/.well-known/", "/static/")):
            return await http_exception_handler(request, exc)
        if exc.status_code == 303:
            return site.RedirectResponse(exc.headers.get("Location", "/"), status_code=303)
        messages = {
            400: ("Check your request", "Please review the form and try again."),
            403: ("Session expired", "Reload the page before submitting again."),
            404: ("Page unavailable", "This page or post is no longer available."),
            409: ("Configuration changed", "Reload the settings before saving again."),
        }
        title, message = messages.get(exc.status_code, ("Request failed", "Please try again."))
        response = site.page(
            request,
            "error.html",
            {
                "title": title,
                "message": message,
                "back": "/me" if request.session.get("did") else "/",
                "back_label": "Back to feed" if request.session.get("did") else "Back to sign in",
            },
        )
        response.status_code = exc.status_code
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_form(request: Request, exc: RequestValidationError):
        if request.url.path.startswith(("/api/", "/xrpc/")):
            return await request_validation_exception_handler(request, exc)
        return await browser_error(request, StarletteHTTPException(400))

    app.include_router(feedgen.router)
    app.include_router(threads_callbacks.router)
    app.include_router(site.router)
    return app
