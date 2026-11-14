# curator-web

Run with `curator-web`. It is a FastAPI app built by `app.create_app()`.

- `feedgen.py`: `did.json`, `describeFeedGenerator`, `getFeedSkeleton` (reads
  `feed_items` only) and `sendInteractions`.
- `feedauth.py`: verifies the service JWT and returns the requester's DID.
- `oauth.py`: atproto OAuth. It uses a loopback client in dev (`HOSTNAME=127.0.0.1`) and a
  confidential client in prod, which needs `data/oauth_jwk.json` from
  `scripts/gen_oauth_key.py`.
- `site.py`: sign-in, the reader's description, feedback, and a feed preview.
- `appview.py`: post and profile hydration for rendering.
- `templates/`, `static/`.
