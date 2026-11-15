import hashlib

import httpx
import pytest
from curator_core.config import clear_settings
from curator_core.testing import add_user
from curator_curation import drive_notes, note_folders, profiles


def reader(conn, did="did:plc:notes-reader"):
    add_user(conn, did)
    return profiles.bootstrap(conn, did)


def test_multiple_links_keep_identical_filenames_independent(conn, monkeypatch):
    uid = reader(conn)
    roots = ["firstfolder123", "secondfolder123"]
    monkeypatch.setattr(
        drive_notes,
        "scan",
        lambda connection, user, root: ({"entry.md": (root, hashlib.sha256(root.encode()).hexdigest())}, {root}),
    )
    note_folders.configure_many(conn, uid, ["drive:" + root for root in roots])
    assert len(note_folders.folders_for(conn, uid)) == 2
    assert {row[0] for row in conn.execute("SELECT text FROM note_versions")} == set(roots)
    retained_id = conn.execute("SELECT id FROM note_versions WHERE text=?", (roots[1],)).fetchone()[0]
    note_folders.configure_many(conn, uid, ["drive:" + roots[1]])
    assert conn.execute("SELECT id FROM note_versions").fetchone()[0] == retained_id
    assert {row[0] for row in conn.execute("SELECT folder_id FROM notes_drive_folders")} == {roots[1]}


def test_failed_link_does_not_stop_other_sources_syncing(conn, monkeypatch):
    from curator_core import db

    uid = reader(conn)
    roots = ["firstfolder123", "secondfolder123"]
    monkeypatch.setattr(drive_notes, "scan", lambda connection, user, root: ({"entry.md": (root, root)}, {root}))
    note_folders.configure_many(conn, uid, ["drive:" + root for root in roots])

    def scan(connection, user, root):
        if root == roots[0]:
            raise ValueError("unavailable")
        return {"entry.md": ("changed", "changed")}, {root}

    monkeypatch.setattr(drive_notes, "scan", scan)
    conn.execute("DELETE FROM kv WHERE key=?", ("notes-sync:" + uid,))
    note_folders.sync(conn, uid)
    assert {row[0] for row in conn.execute("SELECT text FROM note_versions")} == {roots[0], "changed"}
    assert {row["state"] for row in note_folders.folders_for(conn, uid)} == {"ready", "unavailable"}
    assert db.kv_get(conn, "notes-sync:" + uid)


def test_sync_updates_and_removes_snapshots_without_writing_sources(conn, tmp_path, monkeypatch):
    uid = reader(conn)
    folder = tmp_path / "notes"
    folder.mkdir()
    monkeypatch.setenv("AGORA_NOTES_ROOTS", str(folder))
    clear_settings()
    source = folder / "entry.md"
    source.write_bytes(b"# One\r\nOriginal human text\r\n")
    (folder / "ignore.txt").write_text("Not Markdown")
    note_folders.configure(conn, uid, str(folder))
    original_id = conn.execute("SELECT id FROM note_versions").fetchone()[0]
    note_folders.sync(conn, uid)
    assert conn.execute("SELECT id FROM note_versions").fetchone()[0] == original_id
    source.write_text("# Two\nEdited by the human", encoding="utf-8")
    note_folders.sync(conn, uid)
    rows = conn.execute("SELECT * FROM note_versions").fetchall()
    assert len(rows) == 1 and rows[0]["id"] != original_id
    assert source.read_bytes().decode("utf-8") == rows[0]["text"]
    source.unlink()
    note_folders.sync(conn, uid)
    assert not conn.execute("SELECT 1 FROM note_versions").fetchone()


def test_unavailable_folder_keeps_snapshot_and_marks_status(conn, tmp_path, monkeypatch):
    uid = reader(conn)
    folder = tmp_path / "notes"
    folder.mkdir()
    (folder / "entry.md").write_text("Original source")
    monkeypatch.setenv("AGORA_NOTES_ROOTS", str(folder))
    clear_settings()
    note_folders.configure(conn, uid, str(folder))
    moved = tmp_path / "moved"
    folder.rename(moved)
    note_folders.sync(conn, uid)
    assert note_folders.folder_for(conn, uid)["state"] == "unavailable"
    assert conn.execute("SELECT text FROM note_versions").fetchone()[0] == "Original source"
    assert (moved / "entry.md").read_text() == "Original source"


def test_folder_boundaries_and_reader_ownership(conn, tmp_path, monkeypatch):
    uid = reader(conn)
    other = reader(conn, "did:plc:other-notes")
    folder = tmp_path / "notes"
    folder.mkdir()
    (folder / "child").mkdir()
    monkeypatch.setenv("AGORA_NOTES_ROOTS", str(folder))
    clear_settings()
    with pytest.raises(ValueError, match="outside"):
        note_folders.configure(conn, uid, str(tmp_path))
    note_folders.configure(conn, uid, str(folder))
    with pytest.raises(ValueError, match="another reader"):
        note_folders.configure(conn, other, str(folder / "child"))


def drive_client(folder="folder123456", fail=False):
    def handle(request):
        assert request.method == "GET"
        assert request.url.host == "www.googleapis.com"
        if request.url.params.get("alt") == "media":
            return httpx.Response(403 if fail else 200, content=b"# Real source\r\nExact text")
        if request.url.path.endswith("/" + folder):
            return httpx.Response(200, json={"mimeType": drive_notes.FOLDER, "trashed": False})
        return httpx.Response(
            200,
            json={
                "files": [
                    {
                        "id": "markdown123456",
                        "name": "entry.md",
                        "mimeType": "text/markdown",
                        "size": "25",
                        "capabilities": {"canDownload": True},
                    },
                    {"id": "ignored123456", "name": "ignore.txt", "mimeType": "text/plain"},
                ]
            },
        )

    return httpx.Client(transport=httpx.MockTransport(handle))


def test_drive_uses_fixed_api_and_exact_read_only_markdown(conn):
    uid = reader(conn)
    with drive_client() as client:
        found, folders = drive_notes._scan(client, conn, uid, "folder123456")
    assert folders == {"folder123456"}
    assert found["entry.md"][0] == "# Real source\r\nExact text"
    assert len(found) == 1
    assert drive_notes.SCOPE.endswith("/drive.readonly")
    assert drive_notes.folder_id("https://drive.google.com/drive/folders/folder123456?usp=sharing") == "folder123456"
    for value in (
        "https://evil.example/drive/folders/folder123456",
        "http://127.0.0.1/private",
        "https://drive.google.com/file/d/file123456",
    ):
        with pytest.raises(ValueError):
            drive_notes.folder_id(value)


def test_drive_prevents_other_readers_folder_and_denied_downloads(conn):
    uid = reader(conn)
    conn.execute("INSERT INTO notes_drive_folders VALUES ('folder123456','another-reader')")
    with drive_client() as client, pytest.raises(ValueError, match="another reader"):
        drive_notes._scan(client, conn, uid, "folder123456")
    conn.execute("DELETE FROM notes_drive_folders")
    with drive_client(fail=True) as client, pytest.raises(httpx.HTTPStatusError):
        drive_notes._scan(client, conn, uid, "folder123456")


def test_drive_configuration_sync_and_disconnect_preserve_exact_snapshots(conn, monkeypatch):
    uid = reader(conn)
    source = {"entry.md": ("Exact source", "first-hash")}
    monkeypatch.setattr(drive_notes, "scan", lambda *args: (source, {"folder123456"}))
    note_folders.configure(conn, uid, "https://drive.google.com/drive/folders/folder123456")
    row = conn.execute("SELECT * FROM note_versions").fetchone()
    assert row["text"] == "Exact source"
    assert note_folders.folder_label(note_folders.folder_for(conn, uid)).startswith("https://drive.google.com/")

    # A failed provider call must keep the successful original snapshot.
    def unavailable(*args):
        raise ValueError("unavailable")

    monkeypatch.setattr(drive_notes, "scan", unavailable)
    conn.execute("DELETE FROM kv WHERE key=?", ("notes-sync:" + uid,))
    note_folders.sync(conn, uid)
    assert note_folders.folder_for(conn, uid)["state"] == "unavailable"
    assert conn.execute("SELECT text FROM note_versions").fetchone()[0] == "Exact source"
    note_folders.configure(conn, uid, None)
    assert not conn.execute("SELECT 1 FROM note_versions").fetchone()
    assert not conn.execute("SELECT 1 FROM notes_drive_folders").fetchone()


def test_drive_credential_errors_are_sanitized(monkeypatch, conn):
    def bad_credentials():
        raise ValueError("PRIVATE-CREDENTIAL-SENTINEL")

    monkeypatch.setattr(drive_notes, "credentials", bad_credentials)
    with pytest.raises(ValueError) as error:
        drive_notes.scan(conn, "reader", "folder123456")
    assert "PRIVATE-CREDENTIAL-SENTINEL" not in str(error.value)


def test_shared_drive_scans_nested_markdown_and_rejects_incomplete_listing(conn, monkeypatch):
    import json

    uid = reader(conn)
    monkeypatch.setenv("CONTEXT_DEV_API_KEY", "test-key")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    clear_settings()
    original_client = httpx.Client
    state = {"text": b"# Original\r\nLine", "incomplete": False}

    def row(file_id, kind, name):
        return f'<tr data-id="{file_id}" data-target="{kind}"><td><span class="WQJtxb">{name}</span></td></tr>'

    def handle(request):
        if request.url.host == "api.context.dev":
            assert request.headers["authorization"] == "Bearer test-key"
            payload = json.loads(request.content)
            assert payload["maxAgeMs"] == 0
            folder = payload["url"].split("/")[-1]
            rows = (
                row("childfolder123", "folder", "nested")
                if folder == "rootfolder123"
                else row("file123456789", "doc", "entry.md")
            )
            count = 2 if state["incomplete"] else 1
            html = f'<table role="grid" aria-label="Item List" aria-rowcount="{count}">{rows}</table>'
            return httpx.Response(200, json={"html": {"success": True, "data": html}})
        assert request.url.host == "drive.usercontent.google.com"
        assert "authorization" not in request.headers
        return httpx.Response(200, content=state["text"], headers={"content-type": "application/octet-stream"})

    monkeypatch.setattr(
        drive_notes.httpx, "Client", lambda **kwargs: original_client(transport=httpx.MockTransport(handle))
    )
    note_folders.configure(conn, uid, "https://drive.google.com/drive/folders/rootfolder123")
    assert conn.execute("SELECT text FROM note_versions").fetchone()[0] == "# Original\r\nLine"
    state["text"] = b"Changed"
    note_folders.configure(conn, uid, "drive:rootfolder123")
    assert conn.execute("SELECT text FROM note_versions").fetchone()[0] == "Changed"
    state["incomplete"] = True
    with pytest.raises(ValueError, match="incomplete"):
        note_folders.configure(conn, uid, "drive:rootfolder123")
    assert conn.execute("SELECT text FROM note_versions").fetchone()[0] == "Changed"
    clear_settings()


def test_shared_scans_preserve_notes_missing_from_listing(conn, monkeypatch):
    uid = reader(conn)
    monkeypatch.setenv("CONTEXT_DEV_API_KEY", "test-key")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    clear_settings()
    source = {"entry.md": ("Original", "hash")}
    monkeypatch.setattr(drive_notes, "scan", lambda *args: (dict(source), {"rootfolder123"}))
    note_folders.configure(conn, uid, "drive:rootfolder123")
    source.clear()
    note_folders.configure(conn, uid, "drive:rootfolder123")
    assert conn.execute("SELECT text FROM note_versions").fetchone()[0] == "Original"
    clear_settings()


@pytest.mark.parametrize(
    ("html", "success"),
    [
        (
            '<table role="grid" aria-label="Item List" aria-rowcount="2"><tr data-id="file123456789" data-target="doc"><td><span class="WQJtxb">entry.md</span></td></tr></table>',
            True,
        ),
        (
            '<table role="grid" aria-label="Item List" aria-rowcount="1"><tr data-id="file123456789" data-target="unknown"><td><span class="WQJtxb">entry.md</span></td></tr></table>',
            True,
        ),
        (
            '<table role="grid" aria-label="Item List" aria-rowcount="1"><tr data-id="bad" data-target="doc"><td><span class="WQJtxb">entry.md</span></td></tr></table>',
            True,
        ),
        ("<html>Sign in to view this folder</html>", True),
        ('<table role="grid" aria-label="Item List" aria-rowcount="0"></table>', False),
    ],
)
def test_shared_drive_rejects_untrusted_or_incomplete_responses(conn, monkeypatch, html, success):
    uid = reader(conn)
    monkeypatch.setenv("CONTEXT_DEV_API_KEY", "test-key")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    clear_settings()
    original_client = httpx.Client
    calls = []

    def handle(request):
        calls.append(request.url.host)
        return httpx.Response(200, json={"html": {"success": success, "data": html}})

    monkeypatch.setattr(
        drive_notes.httpx, "Client", lambda **kwargs: original_client(transport=httpx.MockTransport(handle))
    )
    with pytest.raises(drive_notes.DriveNotesError):
        note_folders.configure(conn, uid, "drive:rootfolder123")
    assert calls == ["api.context.dev"]
    assert not note_folders.folder_for(conn, uid)
    assert not conn.execute("SELECT 1 FROM note_versions").fetchone()
    clear_settings()


def test_shared_drive_does_not_reuse_an_old_single_file_listing(conn, monkeypatch):
    import json

    uid = reader(conn)
    monkeypatch.setenv("CONTEXT_DEV_API_KEY", "test-key")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    clear_settings()
    original_client = httpx.Client

    def handle(request):
        if request.url.host == "api.context.dev":
            count = 7 if json.loads(request.content).get("maxAgeMs") == 0 else 1
            rows = "".join(
                f'<tr data-id="file12345678{i}" data-target="doc"><td><span class="WQJtxb">note{i}.md</span></td></tr>'
                for i in range(count)
            )
            html = f'<table role="grid" aria-label="Item List" aria-rowcount="{count}">{rows}</table>'
            return httpx.Response(200, json={"html": {"success": True, "data": html}})
        return httpx.Response(200, content=b"Original note", headers={"content-type": "text/markdown"})

    monkeypatch.setattr(
        drive_notes.httpx, "Client", lambda **kwargs: original_client(transport=httpx.MockTransport(handle))
    )
    note_folders.configure(conn, uid, "drive:rootfolder123")
    assert conn.execute("SELECT count(*) FROM note_versions WHERE user_id=?", (uid,)).fetchone()[0] == 7
