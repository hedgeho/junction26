# Source-only web reader

FastAPI/Jinja serves a finite daily selection. Feedback is queued, with fixed status labels. The main page and `/api/digest` return original posts and exact note slices only. Settings provide deliberate access to public/private Markdown configuration, revision restore, delivery settings and read-only note snapshot imports/removal.

Bluesky OAuth identifies the reader. It does not provide X sign-in; X public posts use the normalized pipeline contract and render through their own canonical source URLs. The optional Bluesky feed endpoint projects only Bluesky items from the same digest, without generated feedContext or a popular fallback.

All cookie-authenticated form mutations require a CSRF token. Private responses carry `Cache-Control: no-store`. Input is escaped, source text is not interpreted as HTML, and provider errors are not exposed to the reader. The host must set a strong `SESSION_SECRET`, use HTTPS, enforce a request-body limit (recommend 512 KiB) at its proxy, and back up SQLite and profile revision files consistently.

```sh
uv run curator-web
```

Open the loopback IP in development as described in the root README. No publication/write-to-notes APIs exist. Note removal deletes the imported snapshot and derived matches, not an external original. Profile and feedback retention/account erasure are not automated in this demo.

## Local UI preview

Run `uv run python scripts/preview_ui.py` and open `http://127.0.0.1:8010/me` to explore the reader with clearly labeled fictional sample content. No credentials or live services are required. The preview renders the same templates as the application, but does not save form submissions. `/settings`, `/privacy`, `/` and `/me?empty=1` are also available. The application itself still runs on port 8000.


## Agora: For You source

Sign in at `http://127.0.0.1:8000` (`uv run python -m curator_web`) to grant read-only feed access. Existing users can use **Settings > Your Bluesky source > Connect / reconnect Bluesky**. The source is **For You by spacecowboy17**, pulled using each reader's OAuth session. No Bluesky app password or feed service token is needed. Pulling candidates does not bypass Gemini classification: run `uv run python -m curator_curation run` with configured providers to build the daily selection.

The credential-free UI preview on port 8010 continues to display labeled sample posts; it cannot retrieve a personalized feed. Source connection settings are functional only in the application on port 8000. There is no public fallback when For You requires authentication. The adapter pulls one page on login, stale reader open or explicit refresh; it is not an unattended collector.
