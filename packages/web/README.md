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

## Notes connections

Setup and Settings accept up to ten Google Drive folder links, one per line. With `CONTEXT_DEV_API_KEY`, share each folder as "Anyone with the link" (Viewer). Context.dev processes shared folder pages to read their contents and listings; original Markdown is downloaded directly from Google and analyzed by Gemma on Modal. The form links to the Context.dev privacy policy. Anyone with the link can access shared files. Private folders remain available through a service account, which takes precedence when configured. Only `.md` files are imported recursively as UTF-8, with exact original text. No source files are changed. Folder listings use an unsupported Google Drive web layout, so incomplete or changed layouts fail closed and retain missing snapshots until disconnection. Other sources remove missing files on a successful scan. Each link has independent snapshots and availability; removing one link does not affect the others. Configuration history may retain previously inferred interests.

Production note work runs after feed publication on a background worker. Page requests do not scan folders or wait for matching. Drive sync is throttled to once per minute. Bounded per-note calls evaluate the selected feed in parallel; successful matches arrive through `/api/note-matches` and are added without reloading the page. Empty and successful matches are cached, and failures have bounded retries. `NOTE_MATCH_CONCURRENCY`, `NOTE_MATCH_TOP_NOTES`, `NOTE_MATCH_CHUNKS_PER_NOTE` and `GEMMA_CONCURRENCY` control work; the defaults respect the two concurrent inputs of the warm Modal service.

For operator-managed local imports, the backend retains `note_folders.configure` and `AGORA_NOTES_ROOTS`. Local paths refer to the host and are not offered in the web form. Symlinks/junctions are skipped, and overlapping folders cannot belong to different readers.

For private Drive notes on Hostinger:

1. Enable the Google Drive API in a Google Cloud project and create a service account. It needs no project-wide Drive access or domain-wide delegation.
2. Store its JSON key outside the repository, or within ignored `data/`, readable only by the app service user. Set `GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/drive-service-account.json` in the host's `.env`, shared by web and curation. Restart both processes.
3. Create or use a **notes** folder in Drive containing `.md` files. Share just that folder with the service account's email as **Viewer**. Keep general access **Restricted**.
4. Paste the folder URL in setup or **Settings > Notes** and save. The UI shows the service-account email when credentials are configured. No personal Google sign-in or public link is required.

This adapter uses a read-only service account, not per-user Google OAuth. The deployment operator controls its credential and which folders can be shared with it. Folder/descendant ownership is recorded per Agora reader; a second reader cannot connect an overlapping Drive tree. Google Docs and Drive shortcuts are not imported: this connection is specifically for original `.md` files. A browser folder link alone does not grant the host access.

Scans are bounded to 500 Markdown files, 200,000 characters per file, 10 MB in total; Drive scans also cap subfolders/pages. An incomplete, unauthorized, oversized or unreadable scan retains the previous snapshot. Live Drive access requires the host credentials and a shared folder; automated tests exercise API behavior using an HTTP mock.

## Filtering service status

Local processes read `.env` from their working directory. Start both from the repository root, with provider settings in the root `.env`; `deploy/.env` is not loaded automatically. Environment changes require restarting both processes.

Before feedback, Agora uses a neutral baseline for Gemini filtering and shows up to the delivery limit of real candidates while processing. Feedback can create a public profile for subsequent filtering. Gemini requires `GEMINI_API_KEY` and the running curation worker sharing the web app's `.env` and `DATA_DIR`. The default scorer is the Decisions API with `GEMINI_DECISION_MODEL=gemini-decision-1`; set `SCORING_BACKEND=generative` to use the previous `GEMINI_MODEL` scorer as an explicit rollback. Decision scores use the existing relevance threshold and ranking weights, which have not yet been calibrated against labeled user preferences. Private note and feedback processing requires `GEMMA_URL` and `GEMMA_TOKEN`.

The reader uses shared local `static/app.css` and `static/app.js`, with fingerprinted URLs so updates are visible without a hard refresh. Optional notes controls stay hidden/disabled until selected. Browser errors render in the app, failed sign-in/folder forms preserve input, submission buttons show progress, and feedback dialogs support keyboard focus, Escape and per-post draft isolation. Configuration stays collapsed in Settings.

## Post presentation metadata

The reader renders optional author handles and avatars, rich-text facets, image galleries and external link cards. The public Bluesky AppView supplies these asynchronously for displayed posts; failure leaves the original text visible. Stored media and author metadata fill in when the AppView has none, which covers X posts. Source pipelines supply `author_handle`, `author_avatar` and `media` through the importer (see `packages/core/README.md`). The reader receives `media` as JSON shaped as `{"images":[{"url":"https://...","full_url":"https://...","alt":"..."}],"external":{"url":"https://...","title":"...","description":"...","image":"https://..."}}`; `facets` is a JSON array of `{"start":0,"end":5,"url":"https://..."}` using UTF-8 byte offsets into the exact source text. Images and links must use HTTPS. Facets are not yet persisted by the importer.
