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
    return conn.execute("SELECT * FROM notes_folders WHERE user_id=?", (user_id,)).fetchone()


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


def _remove_snapshots(conn, user_id):
    for row in conn.execute("SELECT note_id FROM notes_files WHERE user_id=?", (user_id,)).fetchall():
        notes.delete_note(conn, user_id, row["note_id"])
    conn.execute("DELETE FROM notes_files WHERE user_id=?", (user_id,))


def _apply(conn, user_id, found, *, preserve_missing=False):
    existing = {
        row["relative_path"]: dict(row)
        for row in conn.execute(
            """SELECT f.relative_path,f.note_id,n.content_hash FROM notes_files f
        JOIN note_versions n ON n.id=f.note_id WHERE f.user_id=?""",
            (user_id,),
        )
    }
    for relative, row in existing.items():
        if (relative not in found and not preserve_missing) or (
            relative in found and row["content_hash"] != found[relative][1]
        ):
            notes.delete_note(conn, user_id, row["note_id"])
            conn.execute("DELETE FROM notes_files WHERE user_id=? AND relative_path=?", (user_id, relative))
    for relative, (text, content_hash) in found.items():
        if relative not in existing or existing[relative]["content_hash"] != content_hash:
            note_id = notes.import_note(conn, user_id, relative, text)
            conn.execute("INSERT INTO notes_files VALUES (?,?,?)", (user_id, relative, note_id))


def configure(conn, user_id: str, value: str | None):
    if value is None:
        conn.execute("DELETE FROM notes_drive_folders WHERE user_id=?", (user_id,))
        _remove_snapshots(conn, user_id)
        conn.execute("DELETE FROM notes_folders WHERE user_id=?", (user_id,))
        return
    if value.startswith(("https://", "http://", "drive:")):
        root = drive_notes.folder_id(value)
        found, folders = drive_notes.scan(conn, user_id, root)
        current = folder_for(conn, user_id)
        if current and current["path"] != "drive:" + root:
            _remove_snapshots(conn, user_id)
        conn.execute("DELETE FROM notes_drive_folders WHERE user_id=?", (user_id,))
        conn.executemany("INSERT INTO notes_drive_folders VALUES (?,?)", [(f, user_id) for f in folders])
        conn.execute(
            "INSERT INTO notes_folders VALUES (?,?,'ready') ON CONFLICT(user_id) DO UPDATE SET path=excluded.path,state='ready'",
            (user_id, "drive:" + root),
        )
        _apply(
            conn,
            user_id,
            found,
            preserve_missing=bool(settings().context_dev_api_key and not settings().google_application_credentials),
        )
        db.kv_set(conn, "notes-sync:" + user_id, str(int(time.time())))
        return
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
    conn.execute("DELETE FROM notes_drive_folders WHERE user_id=?", (user_id,))
    current = folder_for(conn, user_id)
    if current and current["path"] != str(folder):
        _remove_snapshots(conn, user_id)
    conn.execute(
        "INSERT INTO notes_folders VALUES (?,?,'ready') ON CONFLICT(user_id) DO UPDATE SET path=excluded.path,state='ready'",
        (user_id, str(folder)),
    )
    _apply(conn, user_id, found)


def sync(conn, user_id: str):
    current = folder_for(conn, user_id)
    if not current:
        return
    if current["path"].startswith("drive:"):
        if int(db.kv_get(conn, "notes-sync:" + user_id) or "0") + 60 > time.time():
            return
        try:
            configure(conn, user_id, current["path"])
        except ValueError:
            conn.execute("UPDATE notes_folders SET state='unavailable' WHERE user_id=?", (user_id,))
            db.kv_set(conn, "notes-sync:" + user_id, str(int(time.time())))
        return
    try:
        found = _scan(resolve_folder(current["path"]))
    except (OSError, UnicodeError, ValueError):
        conn.execute("UPDATE notes_folders SET state='unavailable' WHERE user_id=?", (user_id,))
        return
    _apply(conn, user_id, found)
    conn.execute("UPDATE notes_folders SET state='ready' WHERE user_id=?", (user_id,))


def setup_done(conn, user_id: str) -> bool:
    return db.kv_get(conn, "setup:" + user_id) == "done"


def folder_label(row) -> str:
    if not row:
        return ""
    value = row["path"]
    return "https://drive.google.com/drive/folders/" + value[6:] if value.startswith("drive:") else value
