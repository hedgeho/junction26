"""atproto OAuth login for the website, via the `atproto` package's OAuthClient.

Dev (hostname is localhost/127.0.0.1): loopback client, no keys, no published
metadata; open the site at http://127.0.0.1:8000.
Prod: confidential client. client_id is the URL of our metadata document, and
we sign client assertions with the ES256 JWK from scripts/gen_oauth_key.py.

Scopes permit only feed reads through the AppView and feed generators. No repo,
post, like, follow, or publishing permissions are requested. Existing identity-only
sessions must reconnect to grant feed access.
"""

import json
from functools import lru_cache

from atproto import AsyncOAuthClient
from atproto_oauth import JsonFileStore

from .config import settings

SCOPE = (
    "atproto rpc:app.bsky.feed.getFeed?aud=did:web:api.bsky.app%23bsky_appview rpc:app.bsky.feed.getFeedSkeleton?aud=*"
)


def is_dev() -> bool:
    host = settings().hostname
    return host.startswith(("localhost", "127.0.0.1")) or host.endswith(".localhost")


def base_url() -> str:
    return "http://127.0.0.1:8000" if is_dev() else f"https://{settings().hostname}"


@lru_cache
def client() -> AsyncOAuthClient:
    cfg = settings()
    stores = {
        "state_store": JsonFileStore(cfg.data_dir / "oauth_state.json"),
        "session_store": JsonFileStore(cfg.data_dir / "oauth_sessions.json"),
    }
    redirect = f"{base_url()}/oauth/callback"
    if is_dev():
        return AsyncOAuthClient.loopback(redirect_uri=redirect, scope=SCOPE, **stores)
    key = json.loads((cfg.data_dir / "oauth_jwk.json").read_text())
    return AsyncOAuthClient.confidential(
        client_id=f"{base_url()}/oauth-client-metadata.json",
        redirect_uri=redirect,
        scope=SCOPE,
        keys=[key],
        metadata={"client_name": "Agora", "client_uri": base_url()},
        **stores,
    )
