# Source-only web reader

FastAPI/Jinja serves a finite daily selection. Feedback is queued, with fixed status labels. The main page and `/api/digest` return original posts and exact note slices only. Settings provide deliberate access to public/private Markdown configuration, revision restore, delivery settings and read-only Markdown folder connections.

Bluesky OAuth identifies the reader. It does not provide X sign-in; X public posts use the normalized pipeline contract and render through their own canonical source URLs. The optional Bluesky feed endpoint projects only Bluesky items from the same digest, without generated feedContext or a popular fallback.

All cookie-authenticated form mutations require a CSRF token. Private responses carry `Cache-Control: no-store`. Input is escaped, source text is not interpreted as HTML, and provider errors are not exposed to the reader. The host must set a strong `SESSION_SECRET`, use HTTPS, enforce a request-body limit (recommend 512 KiB) at its proxy, and back up SQLite and profile revision files consistently.

```sh
uv run curator-web
```

Open the loopback IP in development as described in the root README. No publication/write-to-notes APIs exist. Note removal deletes the imported snapshot and derived matches, not an external original. Profile and feedback retention/account erasure are not automated in this demo.

## Agora sources

Sign in with Bluesky and run the ingest and curation workers. Candidate recall combines posts from followed accounts, public topic matches and recent discovery from the ingested Bluesky and X sources. The discovery pool is not the signed-in user's Bluesky feed. Gemini evaluates candidate batches concurrently; source posts remain visible while filtering runs. No interests are required to start.

## Notes: local or Google Drive

Setup and Settings accept either an existing local directory (default `notes`) or a private Google Drive folder URL. Both read `.md` files recursively as UTF-8, keep exact source text, and ignore other file types. There is no paste editor or sample-data mode. Changes are synced on reader/settings access and before curation; Drive polls at most once per minute. Sources remain untouched. Removing a file removes its imported snapshot/matches on the next successful scan; disconnecting removes the connected folder's snapshots. Previous inferred configuration history remains available. An unavailable source preserves its last snapshot and shows a fixed status.

For local notes, create `notes/` on the application machine. It is excluded from Git. To connect another location, set `AGORA_NOTES_ROOTS` on both web and curation processes to the permitted directory roots, separated by the OS path separator (`:` on Linux, `;` on Windows). The path entered in the browser must be under one of those roots. Links/junctions are skipped, and overlapping folders cannot belong to different readers. Local paths refer to the host, not the browser's filesystem.

For private Drive notes on Hostinger:

1. Enable the Google Drive API in a Google Cloud project and create a service account. It needs no project-wide Drive access or domain-wide delegation.
2. Store its JSON key outside the repository, or within ignored `data/`, readable only by the app service user. Set `GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/drive-service-account.json` in the host's `.env`, shared by web and curation. Restart both processes.
3. Create or use a **notes** folder in Drive containing `.md` files. Share just that folder with the service account's email as **Viewer**. Keep general access **Restricted**.
4. Paste the folder URL in setup or **Settings > Notes** and save. The UI shows the service-account email when credentials are configured. No personal Google sign-in or public link is required.

This adapter uses a read-only service account, not per-user Google OAuth. The deployment operator controls its credential and which folders can be shared with it. Folder/descendant ownership is recorded per Agora reader; a second reader cannot connect an overlapping Drive tree. Google Docs and Drive shortcuts are not imported: this connection is specifically for original `.md` files. A browser folder link alone does not grant the host access.

Scans are bounded to 500 Markdown files, 200,000 characters per file, 10 MB in total; Drive scans also cap subfolders/pages. An incomplete, unauthorized, oversized or unreadable scan retains the previous snapshot. Live Drive access requires the host credentials and a shared folder; automated tests exercise API behavior using an HTTP mock.

## Filtering service status

Local processes read `.env` from their working directory. Start both from the repository root, with provider settings in the root `.env`; `deploy/.env` is not loaded automatically. Environment changes require restarting both processes.

Before feedback, Agora uses a neutral baseline for Gemini filtering and shows up to the delivery limit of real candidates while processing. Feedback can create a public profile for subsequent filtering. Gemini requires `GEMINI_API_KEY`, `GEMINI_MODEL`, and the running curation worker sharing the web app's `.env` and `DATA_DIR`. Private note and feedback processing requires `GEMMA_URL` and `GEMMA_TOKEN`.

The reader uses shared local `static/app.css` and `static/app.js`, with fingerprinted URLs so updates are visible without a hard refresh. Optional notes controls stay hidden/disabled until selected. Browser errors render in the app, failed sign-in/folder forms preserve input, submission buttons show progress, and feedback dialogs support keyboard focus, Escape and per-post draft isolation. Configuration stays collapsed in Settings.

## Post presentation metadata

The reader renders optional author handles and avatars, rich-text facets, image galleries and external link cards. The public Bluesky AppView supplies these asynchronously for displayed posts; failure leaves the original text visible. Other source pipelines can add nullable `author_handle`, `author_avatar`, `media`, and `facets` columns to `social_posts`. `media` is JSON shaped as `{"images":[{"url":"https://...","full_url":"https://...","alt":"..."}],"external":{"url":"https://...","title":"...","description":"...","image":"https://..."}}`; `facets` is a JSON array of `{"start":0,"end":5,"url":"https://..."}` using UTF-8 byte offsets into the exact source text. Fields are optional and images and links must use HTTPS. The importer does not yet persist these extra fields; coordinate schema and ingestion with the data pipeline before relying on them for X.
