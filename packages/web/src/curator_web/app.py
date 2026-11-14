from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from . import feedgen, oauth, site
from .config import settings


def create_app() -> FastAPI:
    app = FastAPI(title="Agora")
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
    app.include_router(site.router)
    return app
