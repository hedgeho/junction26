# Source-only web reader

FastAPI/Jinja serves a finite daily selection. Feedback is queued, with fixed status labels. The main page and `/api/digest` return original posts and exact note slices only. Settings provide deliberate access to public/private Markdown configuration, revision restore, delivery settings and read-only note snapshot imports/removal.

Bluesky OAuth identifies the reader. It does not provide X sign-in; X public posts use the normalized pipeline contract and render through their own canonical source URLs. The optional Bluesky feed endpoint projects only Bluesky items from the same digest, without generated feedContext or a popular fallback.

All cookie-authenticated form mutations require a CSRF token. Private responses carry `Cache-Control: no-store`. Input is escaped, source text is not interpreted as HTML, and provider errors are not exposed to the reader. The host must set a strong `SESSION_SECRET`, use HTTPS, enforce a request-body limit (recommend 512 KiB) at its proxy, and back up SQLite and profile revision files consistently.

```sh
uv run curator-web
```

Open the loopback IP in development as described in the root README. No publication/write-to-notes APIs exist. Note removal deletes the imported snapshot and derived matches, not an external original. Profile and feedback retention/account erasure are not automated in this demo.
