"""Read-only Markdown folders, bounded scans and per-reader snapshot ownership."""

import hashlib
import os
import time
from pathlib import Path

from curator_core import db

from . import drive_notes, notes
from .config import settings

MAX_FILES = 500
MAX_BYTES = 10_000_000


def allowed_roots() -> list[Path]:
    configured = settings().agora_notes_roots
    return [Path(p).expanduser().resolve() for p in configured.split(os.pathsep) if p] or [
        (Path.cwd() / "notes").resolve()
    ]


def folder_for(conn, user_id: str):
    return conn.execute("SELECT * FROM notes_folders WHERE user_id=? ORDER BY path", (user_id,)).fetchone()


def folders_for(conn, user_id: str):
    return conn.execute("SELECT * FROM notes_folders WHERE user_id=? ORDER BY path", (user_id,)).fetchall()


def folder_links(conn, user_id: str) -> str:
    return "\n".join(folder_label(row) for row in folders_for(conn, user_id) if row["path"].startswith("drive:"))


def resolve_folder(value: str) -> Path:
    if not value.strip():
        raise ValueError("Choose a Markdown folder.")
    candidate = Path(value.strip()).expanduser().absolute()
    # Do not follow a selected symlink or junction into an unrelated directory.
    if any(p.is_symlink() or p.is_junction() for p in (candidate, *candidate.parents)):
        raise ValueError("Choose a folder without symbolic links.")
    candidate = candidate.resolve()
    if not any(candidate.is_relative_to(root) for root in allowed_roots()):
        raise ValueError("Folder is outside the notes locations enabled on this host (AGORA_NOTES_ROOTS).")
    if not candidate.is_dir():
        raise ValueError("Folder does not exist on this machine.")
    return candidate


def _scan(folder: Path) -> dict[str, tuple[str, str]]:
    found = {}
    total = 0
    directory_count = 0

    def scan_error(error):
        raise error

    for directory, dirs, files in os.walk(folder, followlinks=False, onerror=scan_error):
        directory_count += 1
        if directory_count > 1000:
            raise ValueError("Too many note directories.")
        parent = Path(directory)
        dirs[:] = [
            d
            for d in dirs
            if not d.startswith(".") and not (parent / d).is_symlink() and not (parent / d).is_junction()
        ]
        for name in sorted(files):
            path = parent / name
            if path.suffix.lower() != ".md" or name.startswith(".") or path.is_symlink():
                continue
            if len(found) >= MAX_FILES or path.stat().st_size > 800_000:
                raise ValueError("Notes exceed the folder import limits.")
            raw = path.read_bytes()
            total += len(raw)
            if total > MAX_BYTES:
                raise ValueError("Notes exceed the folder import limits.")
            text = raw.decode("utf-8")
            relative = path.relative_to(folder).as_posix()
            if not text.strip():
                continue
            if len(text) > 200000 or len(relative) > 200 or "\x00" in text:
                raise ValueError("A Markdown file is too large or invalid.")
            found[relative] = (text, hashlib.sha256(text.encode()).hexdigest())
    return found


def _remove_snapshots(conn, user_id, source_path):
    for row in conn.execute(
        "SELECT note_id FROM notes_files WHERE user_id=? AND source_path=?", (user_id, source_path)
    ).fetchall():
        notes.delete_note(conn, user_id, row["note_id"])
    conn.execute("DELETE FROM notes_files WHERE user_id=? AND source_path=?", (user_id, source_path))


def _apply(conn, user_id, source_path, found, *, preserve_missing=False):
    existing = {
        row["relative_path"]: dict(row)
        for row in conn.execute(
            """SELECT f.relative_path,f.note_id,n.content_hash FROM notes_files f
        JOIN note_versions n ON n.id=f.note_id WHERE f.user_id=? AND f.source_path=?""",
            (user_id, source_path),
        )
    }
    for relative, row in existing.items():
        if (relative not in found and not preserve_missing) or (
            relative in found and row["content_hash"] != found[relative][1]
        ):
            notes.delete_note(conn, user_id, row["note_id"])
            conn.execute(
                "DELETE FROM notes_files WHERE user_id=? AND source_path=? AND relative_path=?",
                (user_id, source_path, relative),
            )
    for relative, (text, content_hash) in found.items():
        if relative not in existing or existing[relative]["content_hash"] != content_hash:
            note_id = notes.import_note(conn, user_id, relative, text)
            conn.execute("INSERT INTO notes_files VALUES (?,?,?,?)", (user_id, relative, note_id, source_path))


def configure(conn, user_id: str, value: str | None):
    configure_many(conn, user_id, [value] if value else [])


def _prepare(conn, user_id, value):
    if value.startswith(("https://", "http://", "drive:")):
        root = drive_notes.folder_id(value)
        found, folders = drive_notes.scan(conn, user_id, root)
        return "drive:" + root, found, folders
    folder = resolve_folder(value)
    for row in conn.execute("SELECT user_id,path FROM notes_folders"):
        if row["path"].startswith("drive:"):
            continue
        other = Path(row["path"])
        if row["user_id"] != user_id and (folder.is_relative_to(other) or other.is_relative_to(folder)):
            raise ValueError("This notes location is already connected to another reader.")
    try:
        found = _scan(folder)
    except (OSError, UnicodeError) as error:
        raise ValueError("Could not read the Markdown folder.") from error
    return str(folder), found, set()


def _save_source(conn, user_id, path, found, folders):
    owner = conn.execute("SELECT user_id FROM notes_folders WHERE path=?", (path,)).fetchone()
    if owner and owner["user_id"] != user_id:
        raise ValueError("This notes folder is already connected to another reader.")
    conn.execute(
        "INSERT INTO notes_folders VALUES (?,?,'ready') ON CONFLICT(user_id,path) DO UPDATE SET state='ready'",
        (user_id, path),
    )
    conn.execute("DELETE FROM notes_source_drive_folders WHERE user_id=? AND source_path=?", (user_id, path))
    for folder in folders:
        claim = conn.execute("SELECT user_id FROM notes_drive_folders WHERE folder_id=?", (folder,)).fetchone()
        if claim and claim["user_id"] != user_id:
            raise ValueError("This notes folder is already connected to another reader.")
        conn.execute("INSERT OR IGNORE INTO notes_drive_folders VALUES (?,?)", (folder, user_id))
        conn.execute("INSERT INTO notes_source_drive_folders VALUES (?,?,?)", (user_id, path, folder))
    _apply(
        conn,
        user_id,
        path,
        found,
        preserve_missing=bool(
            path.startswith("drive:")
            and settings().context_dev_api_key
            and not settings().google_application_credentials
        ),
    )


def configure_many(conn, user_id: str, values: list[str]):
    values = list(dict.fromkeys(value.strip() for value in values if value.strip()))
    if len(values) > 10:
        raise ValueError("Connect up to 10 folders.")
    prepared = {
        _path: (found, folders) for _path, found, folders in (_prepare(conn, user_id, value) for value in values)
    }
    with db.tx(conn):
        for row in folders_for(conn, user_id):
            if row["path"] not in prepared:
                _remove_snapshots(conn, user_id, row["path"])
                conn.execute("DELETE FROM notes_folders WHERE user_id=? AND path=?", (user_id, row["path"]))
                conn.execute(
                    "DELETE FROM notes_source_drive_folders WHERE user_id=? AND source_path=?", (user_id, row["path"])
                )
        for path, (found, folders) in prepared.items():
            _save_source(conn, user_id, path, found, folders)
        _release_claims(conn, user_id)
    db.kv_set(conn, "notes-sync:" + user_id, str(int(time.time())))


def _release_claims(conn, user_id):
    conn.execute(
        "DELETE FROM notes_drive_folders WHERE user_id=? AND folder_id NOT IN (SELECT folder_id FROM notes_source_drive_folders WHERE user_id=?)",
        (user_id, user_id),
    )


def sync(conn, user_id: str):
    recent = int(db.kv_get(conn, "notes-sync:" + user_id) or "0") + 60 > time.time()
    for current in folders_for(conn, user_id):
        if current["path"].startswith("drive:") and recent:
            continue
        try:
            path, found, folders = _prepare(conn, user_id, current["path"])
            with db.tx(conn):
                if conn.execute("SELECT 1 FROM notes_folders WHERE user_id=? AND path=?", (user_id, path)).fetchone():
                    _save_source(conn, user_id, path, found, folders)
                    _release_claims(conn, user_id)
        except (OSError, UnicodeError, ValueError):
            conn.execute(
                "UPDATE notes_folders SET state='unavailable' WHERE user_id=? AND path=?", (user_id, current["path"])
            )
    if not recent:
        db.kv_set(conn, "notes-sync:" + user_id, str(int(time.time())))


def setup_done(conn, user_id: str) -> bool:
    return db.kv_get(conn, "setup:" + user_id) == "done"


def folder_label(row) -> str:
    if not row:
        return ""
    value = row["path"]
    return "https://drive.google.com/drive/folders/" + value[6:] if value.startswith("drive:") else value
