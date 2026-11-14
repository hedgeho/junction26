# Source-only web reader

FastAPI/Jinja serves a finite daily selection. Feedback is queued, with fixed status labels. The main page and `/api/digest` return original posts and exact note slices only. Settings provide deliberate access to public/private Markdown configuration, revision restore, delivery settings and read-only Markdown folder connections.

Bluesky OAuth identifies the reader. It does not provide X sign-in; X public posts use the normalized pipeline contract and render through their own canonical source URLs. The optional Bluesky feed endpoint projects only Bluesky items from the same digest, without generated feedContext or a popular fallback.

All cookie-authenticated form mutations require a CSRF token. Private responses carry `Cache-Control: no-store`. Input is escaped, source text is not interpreted as HTML, and provider errors are not exposed to the reader. The host must set a strong `SESSION_SECRET`, use HTTPS, enforce a request-body limit (recommend 512 KiB) at its proxy, and back up SQLite and profile revision files consistently.

```sh
uv run curator-web
```

Open the loopback IP in development as described in the root README. No publication/write-to-notes APIs exist. Note removal deletes the imported snapshot and derived matches, not an external original. Profile and feedback retention/account erasure are not automated in this demo.

## Agora: For You source

Sign in at `http://127.0.0.1:8000` (`uv run python -m curator_web`) to grant read-only feed access. First sign-in opens setup; existing users can use **Settings > Bluesky > Reconnect**. The source is **For You by spacecowboy17**, pulled using each reader's OAuth session. No Bluesky app password or feed service token is needed. Pulling candidates does not bypass Gemini classification: run `uv run python -m curator_curation run` with configured providers to build the daily selection.

There is no public fallback when For You requires authentication. The adapter pulls one page on login, stale reader open or explicit refresh; it is not an unattended collector.

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

Before a reader has a public profile, Agora displays the first 10 original posts in their authenticated For You snapshot (or their chosen delivery limit). This requires no model call or invented interests. Feedback is queued with the original post, and Gemma can create the first public profile from that feedback. Once a public profile exists, a filtered selection requires `GEMINI_API_KEY`, `GEMINI_MODEL`, and a running `uv run python -m curator_curation run` process sharing the same `.env` and `DATA_DIR` as web. Settings exposes fixed service states: not configured, offline, processing, failed or ready. Private note/feedback processing requires `GEMMA_URL` and `GEMMA_TOKEN`; unprocessed feedback remains saved without pretending an update is running.

The reader uses shared local `static/app.css` and `static/app.js`, with fingerprinted URLs so updates are visible without a hard refresh. Optional notes controls stay hidden/disabled until selected. Browser errors render in the app, failed sign-in/folder forms preserve input, submission buttons show progress, and feedback dialogs support keyboard focus, Escape and per-post draft isolation. Configuration stays collapsed in Settings.
