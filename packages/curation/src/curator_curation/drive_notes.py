"""Read Markdown blobs from a privately shared Drive folder, never write to Drive."""

import hashlib
import re
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
