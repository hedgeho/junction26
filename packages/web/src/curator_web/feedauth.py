"""Verify the service-auth JWT the AppView attaches to feed requests.

The token is signed with the *requesting user's* atproto signing key, so it
tells us who is reading the feed. aud must be our service DID; lxm, when
present, must name the method being called.
"""

import base64
import json
import logging

from atproto import AsyncIdResolver
from atproto_server.auth.jwt import verify_jwt_async

from .config import settings

log = logging.getLogger(__name__)
_resolver = AsyncIdResolver()


async def _signing_key(did: str, force_refresh: bool) -> str:
    return await _resolver.did.resolve_atproto_key(did, force_refresh)


def _claims(token: str) -> dict:
    part = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


async def requester_did(authorization: str | None, lxm: str) -> str | None:
    """DID of the authenticated requester, or None for anonymous/invalid requests."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = await verify_jwt_async(token, _signing_key, own_did=settings().service_did)
        claimed = _claims(token).get("lxm")
        if claimed is not None and claimed != lxm:
            return None
    except Exception as e:  # noqa: BLE001 - any failure means unauthenticated
        log.info("rejected feed JWT: %s", e)
        return None
    return payload.iss.split("#")[0]
