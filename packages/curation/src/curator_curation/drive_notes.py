"""Read Markdown blobs from a privately shared Drive folder, never write to Drive."""

import hashlib
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import httpx
from google.auth.transport.requests import Request
from google.oauth2 import service_account

from .config import settings

API = "https://www.googleapis.com/drive/v3/files"
SCOPE = "https://www.googleapis.com/auth/drive.readonly"
FOLDER = "application/vnd.google-apps.folder"
ID = re.compile(r"[A-Za-z0-9_-]{10,200}\Z")


class DriveNotesError(ValueError):
    pass


def folder_id(value: str) -> str:
    if value.startswith("drive:") and ID.fullmatch(value[6:]):
        return value[6:]
    url = urlparse(value)
    match = re.fullmatch(r"/drive/(?:u/\d+/)?folders/([A-Za-z0-9_-]{10,200})/?", url.path)
    if url.scheme != "https" or url.netloc != "drive.google.com" or not match:
        raise DriveNotesError("Use a Google Drive folder link.")
    return match[1]


def credentials():
    path = settings().google_application_credentials
    if not path:
        raise DriveNotesError("Google Drive is not configured on this host.")
    return service_account.Credentials.from_service_account_file(path, scopes=[SCOPE])


def viewer_email() -> str:
    try:
        return credentials().service_account_email
    except Exception:  # noqa: BLE001 - no credential errors in the UI
        return ""


def scan(conn, user_id: str, root: str) -> tuple[dict, set[str]]:
    if not settings().google_application_credentials and settings().context_dev_api_key:
        return scan_shared(conn, user_id, root)
    try:
        creds = credentials()
        creds.refresh(Request())
        with httpx.Client(
            headers={"Authorization": "Bearer " + creds.token}, timeout=30, follow_redirects=False
        ) as client:
            return _scan(client, conn, user_id, root)
    except DriveNotesError:
        raise
    except Exception:  # noqa: BLE001 - credentials and provider errors can include sensitive values
        raise DriveNotesError("Could not read Drive. Share the notes folder with the app as a Viewer.") from None


def _scan(client, conn, user_id, root):
    found, folders = {}, set()
    pending = [(root, "")]
    total = 0
    requests = 0
    while pending:
        current, prefix = pending.pop()
        if current in folders:
            continue
        if len(folders) >= 50:
            raise DriveNotesError("Too many note folders.")
        claim = conn.execute("SELECT user_id FROM notes_drive_folders WHERE folder_id=?", (current,)).fetchone()
        if claim and claim["user_id"] != user_id:
            raise DriveNotesError("This notes folder is already connected to another reader.")
        folders.add(current)
        metadata = client.get(f"{API}/{current}", params={"fields": "mimeType,trashed", "supportsAllDrives": "true"})
        metadata.raise_for_status()
        if metadata.json().get("mimeType") != FOLDER or metadata.json().get("trashed"):
            raise DriveNotesError("Choose an available Google Drive folder.")
        page_token = None
        while True:
            requests += 1
            if requests > 100:
                raise DriveNotesError("Notes exceed the folder import limits.")
            params = {
                "q": f"'{current}' in parents and trashed=false",
                "pageSize": 100,
                "fields": "nextPageToken,incompleteSearch,files(id,name,mimeType,size,capabilities(canDownload))",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            if page_token:
                params["pageToken"] = page_token
            response = client.get(API, params=params)
            response.raise_for_status()
            body = response.json()
            if body.get("incompleteSearch"):
                raise DriveNotesError("Drive returned an incomplete folder. Try again.")
            for item in body.get("files", []):
                native_id, name = item["id"], item["name"]
                if not ID.fullmatch(native_id) or "/" in name or "\\" in name or name in (".", ".."):
                    raise DriveNotesError("Invalid Drive file name.")
                if name.startswith("."):
                    continue
                relative = prefix + name
                if item["mimeType"] == FOLDER:
                    pending.append((native_id, relative + "/"))
                    continue
                if Path(name).suffix.lower() != ".md" or item["mimeType"].startswith("application/vnd.google-apps."):
                    continue
                if not item.get("capabilities", {}).get("canDownload"):
                    raise DriveNotesError("A Markdown file cannot be downloaded.")
                if len(found) >= 500 or int(item.get("size", 0)) > 800000 or len(relative) > 200:
                    raise DriveNotesError("Notes exceed the folder import limits.")
                raw = bytearray()
                with client.stream(
                    "GET", f"{API}/{native_id}", params={"alt": "media", "supportsAllDrives": "true"}
                ) as download:
                    download.raise_for_status()
                    for chunk in download.iter_bytes():
                        raw.extend(chunk)
                        total += len(chunk)
                        if len(raw) > 800000 or total > 10_000_000:
                            raise DriveNotesError("Notes exceed the folder import limits.")
                text = raw.decode("utf-8")
                if not text.strip():
                    continue
                if len(text) > 200000 or "\x00" in text or relative in found:
                    raise DriveNotesError("A Markdown file is invalid or has a duplicate name.")
                found[relative] = (text, hashlib.sha256(text.encode()).hexdigest())
            page_token = body.get("nextPageToken")
            if not page_token:
                break
    return found, folders


class _FolderPage(HTMLParser):
    """Read the rendered Drive item grid, rejecting partial or changed layouts."""

    def __init__(self):
        super().__init__()
        self.expected = None
        self.in_grid = False
        self.row = None
        self.in_name = False
        self.rows = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table" and a.get("role") == "grid" and a.get("aria-label") == "Item List":
            if self.expected is not None:
                raise DriveNotesError("Could not verify the Drive folder listing.")
            self.expected = int(a.get("aria-rowcount", "-1"))
            self.in_grid = True
        elif self.in_grid and tag == "tr" and a.get("data-id"):
            self.row = {"id": a["data-id"], "type": a.get("data-target"), "name": ""}
        elif self.row is not None and not self.row["name"] and tag == "span" and "WQJtxb" in a.get("class", "").split():
            self.in_name = True

    def handle_data(self, data):
        if self.in_name and self.row is not None:
            self.row["name"] += data

    def handle_endtag(self, tag):
        if tag == "span" and self.in_name:
            self.in_name = False
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None
        elif tag == "table" and self.in_grid:
            self.in_grid = False


def scan_shared(conn, user_id, root):
    """Read link-shared folders via Context.dev and download original bytes from Drive."""
    try:
        return _scan_shared(conn, user_id, root)
    except DriveNotesError:
        raise
    except Exception:  # noqa: BLE001 - provider errors may contain credentials or private source data.
        raise DriveNotesError("Could not read the shared folder. Check its link-sharing settings.") from None


def _scan_shared(conn, user_id, root):
    found, folders, pending = {}, set(), [(root, "")]
    total = 0
    with httpx.Client(timeout=45, follow_redirects=False) as client:
        while pending:
            current, prefix = pending.pop()
            if current in folders:
                raise DriveNotesError("The folder contains a duplicate or cyclic folder.")
            if len(folders) >= 50:
                raise DriveNotesError("Too many note folders.")
            claim = conn.execute("SELECT user_id FROM notes_drive_folders WHERE folder_id=?", (current,)).fetchone()
            if claim and claim["user_id"] != user_id:
                raise DriveNotesError("This notes folder is already connected to another reader.")
            folders.add(current)
            response = client.post(
                "https://api.context.dev/v1/web/scrape",
                headers={"Authorization": "Bearer " + settings().context_dev_api_key},
                json={"url": "https://drive.google.com/drive/folders/" + current, "formats": {"html": True}},
            )
            response.raise_for_status()
            result = response.json().get("html") or {}
            html = result.get("data")
            if not result.get("success") or not isinstance(html, str) or len(html) > 10_000_000:
                raise DriveNotesError("Could not verify the Drive folder listing.")
            page = _FolderPage()
            page.feed(html)
            if page.expected is None or page.expected < 0 or len(page.rows) != page.expected:
                raise DriveNotesError("Drive returned an incomplete folder listing.")
            ids = set()
            for item in page.rows:
                native_id, name, kind = item["id"], item["name"], item["type"]
                if (
                    not ID.fullmatch(native_id)
                    or native_id in ids
                    or not name
                    or "/" in name
                    or "\\" in name
                    or name in (".", "..")
                ):
                    raise DriveNotesError("Invalid Drive folder listing.")
                ids.add(native_id)
                if name.startswith("."):
                    continue
                relative = prefix + name
                if len(relative) > 200:
                    raise DriveNotesError("Notes exceed the folder import limits.")
                if kind == "folder":
                    pending.append((native_id, relative + "/"))
                    continue
                if kind != "doc":
                    raise DriveNotesError("An unsupported Drive item prevents a complete scan.")
                if Path(name).suffix.lower() != ".md":
                    continue
                if len(found) >= 500 or relative in found:
                    raise DriveNotesError("Notes exceed the folder import limits or contain duplicate names.")
                with client.stream(
                    "GET",
                    "https://drive.usercontent.google.com/download",
                    params={"export": "download", "id": native_id},
                ) as download:
                    download.raise_for_status()
                    if download.headers.get("content-type", "").startswith("text/html"):
                        raise DriveNotesError("A Markdown file cannot be downloaded publicly.")
                    raw = bytearray()
                    for chunk in download.iter_bytes():
                        raw.extend(chunk)
                        total += len(chunk)
                        if len(raw) > 800_000 or total > 10_000_000:
                            raise DriveNotesError("Notes exceed the folder import limits.")
                text = raw.decode("utf-8")
                if len(text) > 200_000 or "\x00" in text:
                    raise DriveNotesError("A Markdown file is invalid.")
                if text.strip():
                    found[relative] = (text, hashlib.sha256(text.encode()).hexdigest())
    return found, folders
