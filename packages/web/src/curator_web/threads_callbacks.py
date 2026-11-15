"""Callbacks Meta requires for Threads apps.

Agora stores nothing about Threads users: one operator token searches public posts. These
endpoints therefore only acknowledge, and the status page explains what is retained.
"""

import secrets

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .config import settings

# Meta sends these server to server, so they cannot carry a CSRF token.
router = APIRouter()


@router.post("/threads/deauthorize")
def deauthorize() -> JSONResponse:
    return JSONResponse({})


@router.post("/threads/data-deletion")
def data_deletion() -> JSONResponse:
    return JSONResponse(
        {
            "url": f"https://{settings().hostname}/data-deletion",
            "confirmation_code": secrets.token_urlsafe(16),
        }
    )
