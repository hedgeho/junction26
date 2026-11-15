from curator_curation import profiles


def test_full_diff_includes_headings_all_changes_and_ambiguous_prefixes(conn):
    uid = profiles.reader_id(conn, "did:plc:diffs")
    old = "# Old heading\n-- removed text\n" + "".join(f"- old {i}\n" for i in range(9))
    new = "# New heading\n++ added text\n" + "".join(f"- new {i} \u03bb\n" for i in range(9))
    first = profiles.save(conn, uid, "public", old, None)
    second = profiles.save(conn, uid, "public", new, first)
    diff = profiles.revision_diff(conn, uid, second)
    assert diff["revision"] == second and diff["parent_revision"] == first
    assert diff["filename"] == "feed-curation.md" and diff["changed"]
    assert diff["added"] == diff["removed"] == 11
    added = [line["text"] for line in diff["lines"] if line["type"] == "add"]
    removed = [line["text"] for line in diff["lines"] if line["type"] == "remove"]
    assert "+# New heading" in added and "+++ added text" in added
    assert "-# Old heading" in removed and "--- removed text" in removed
    assert "+- new 8 \u03bb" in added
    # An even newer edit cannot change the parent comparison for this revision.
    profiles.save(conn, uid, "public", "# Latest unrelated revision\n", second)
    assert profiles.revision_diff(conn, uid, second) == diff


def test_first_revision_and_unchanged_content(conn):
    uid = profiles.reader_id(conn, "did:plc:diffs")
    first = profiles.save(conn, uid, "private", "# Note matching\n", None)
    diff = profiles.revision_diff(conn, uid, first)
    assert diff["filename"] == "note-matching.md"
    assert diff["added"] == 1 and diff["removed"] == 0 and diff["parent_revision"] is None
    second = profiles.save(conn, uid, "private", "# Note matching\n", first)
    same = profiles.revision_diff(conn, uid, second)
    assert not same["changed"] and not same["lines"] and not same["unavailable"]


def test_line_ending_only_edits_and_final_newline_are_visible(conn):
    uid = profiles.reader_id(conn, "did:plc:diffs")
    first = profiles.save(conn, uid, "public", "# Interests\r\n\r\n- \u03bb\r\n", None)
    second = profiles.save(conn, uid, "public", "# Interests\n\n- \u03bb", first)
    diff = profiles.revision_diff(conn, uid, second)
    assert diff["changed"] and not diff["lines"]
    assert diff["format_changes"] == ["Line endings changed to LF.", "Final newline removed."]


def test_diff_ownership_and_unreadable_parent_fail_closed(conn):
    uid = profiles.reader_id(conn, "did:plc:diffs")
    other = profiles.reader_id(conn, "did:plc:other")
    first = profiles.save(conn, uid, "public", "# Private preferences\n", None)
    second = profiles.save(conn, uid, "public", "# Updated preferences\n", first)
    assert profiles.revision_diff(conn, other, second) is None
    assert profiles.revision_diff(conn, uid, "unknown") is None
    profiles._path(uid, "public", first).write_text("tampered", encoding="utf-8")
    diff = profiles.revision_diff(conn, uid, second)
    assert diff["unavailable"] and not diff["lines"]
