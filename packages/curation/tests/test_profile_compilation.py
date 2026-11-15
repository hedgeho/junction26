import json

import pytest
from curator_core.testing import add_user, insert_post
from curator_curation import jobs, profile_edits, profiles
from curator_curation.llm.gemma import GemmaClient
from curator_curation.llm.schemas import ProfileEdits, ProfileProposal

ORIGINAL = (
    "# Feed\n\n## Goals\nCompilers.\n\n- [topic] Compiler benchmarks\n- [rule] No launch hype\n\n## Notes\nOld goals.\n"
)


@pytest.mark.parametrize(
    "instruction,document,terms",
    [
        ("reset my algo", "", []),
        ("Start over without any remembered preferences", "", []),
        (
            "Replace all my old interests with only landscape photography",
            "# Feed\n\n- [topic] Landscape photography\n",
            ["landscape photography"],
        ),
        (
            "Simplify everything into one rule: evidence over hype",
            "- [rule] Prefer evidence over promotional claims\n",
            [],
        ),
    ],
)
def test_model_can_choose_any_replacement_scope(monkeypatch, instruction, document, terms):
    def call(self, prompt, payload, schema, max_tokens):
        assert payload["current_markdown"] == ORIGINAL
        assert payload["feedback"] == instruction
        assert "No command phrase is required" in prompt
        return ProfileEdits(replacement=ProfileProposal(markdown=document, search_terms=terms))

    monkeypatch.setattr(GemmaClient, "_call", call)
    proposal = GemmaClient("https://gemma.example/v1", "t", "m").update_profile(ORIGINAL, instruction, ["compilers"])
    assert proposal.markdown == document
    assert proposal.search_terms == terms


def test_complete_replacement_is_authoritative_over_redundant_patch_fields():
    edits = ProfileEdits(replacement=ProfileProposal(markdown=""), remove=[0], add_terms=["old search"])
    assert profile_edits.apply(ORIGINAL, ["compilers"], edits) == ("", [])


def test_noop_preserves_document_bytes_and_terms():
    assert profile_edits.apply(ORIGINAL, ["compilers"], ProfileEdits()) == (ORIGINAL, ["compilers"])


def test_full_rewrite_can_exceed_small_patch_limits():
    document = "# Broad new interests\n\n" + "".join(f"- [topic] Research field {number}\n" for number in range(30))
    edits = ProfileEdits(replacement=ProfileProposal(markdown=document, search_terms=["research"]))
    assert profile_edits.apply(ORIGINAL, ["compilers"], edits) == (document, ["research"])


@pytest.mark.parametrize("category,text", [("feedback", "I like photos"), ("topic", "I want more of this")])
def test_replacements_still_reject_raw_or_unresolved_feedback(monkeypatch, category, text):
    monkeypatch.setattr(
        GemmaClient,
        "_call",
        lambda *args, **kwargs: ProfileEdits(replacement=ProfileProposal(markdown=f"- [{category}] {text}\n")),
    )
    with pytest.raises(ValueError, match="standalone curation principles"):
        GemmaClient("https://gemma.example/v1", "t", "m").update_profile(ORIGINAL, "More photos")


@pytest.mark.parametrize("mode", ["preference", "more"])
@pytest.mark.parametrize("scope", ["none", "post", "feed"])
def test_clear_profile_survives_all_feedback_paths_and_keeps_restore_history(conn, monkeypatch, mode, scope):
    did = "did:plc:compile"
    add_user(conn, did, "Compiler research")
    uid = profiles.bootstrap(conn, did)
    previous = profiles.save(conn, uid, "public", ORIGINAL, profiles.head(conn, uid)["id"], terms=["compilers"])
    post_key = "bluesky:" + insert_post(conn, "did:plc:author", "source", "A compiler benchmark")

    def call(self, prompt, payload, schema, max_tokens):
        assert "reset my algo" in payload["feedback"]
        return ProfileEdits(replacement=ProfileProposal(markdown="", search_terms=[]))

    monkeypatch.setattr(GemmaClient, "_call", call)
    model = GemmaClient("https://gemma.example/v1", "t", "m")
    job = jobs.enqueue(
        conn,
        uid,
        "public",
        "reset my algo",
        "clear",
        post_key if scope == "post" else None,
        mode=mode,
        feed_context=json.dumps([{"text": "A compiler benchmark"}]) if scope == "feed" else "",
    )
    assert jobs.process_one(conn, model)
    assert conn.execute("SELECT state FROM feedback_jobs WHERE id=?", (job,)).fetchone()[0] == "applied"
    assert profiles.markdown(conn, uid) == ""
    assert json.loads(profiles.head(conn, uid)["search_terms"]) == []
    assert profiles.head(conn, uid)["parent_id"] == previous
    assert profiles.revision_diff(conn, uid, profiles.head(conn, uid)["id"])["removed"] > 0
    profiles.bootstrap(conn, did)
    assert profiles.markdown(conn, uid) == ""
    jobs.enqueue(conn, uid, "public", "More of this", "unresolvable")
    assert jobs.process_one(conn, model)
    assert profiles.markdown(conn, uid) == ""
    assert profiles._path(uid, "public", previous).read_text(encoding="utf-8") == ORIGINAL


@pytest.mark.parametrize("mode", ["preference", "more"])
def test_job_honors_term_only_changes(conn, mode):
    did = "did:plc:terms"
    add_user(conn, did, "Compilers")
    uid = profiles.bootstrap(conn, did)

    class Model:
        def update_profile(self, current, feedback, terms=()):
            return ProfileProposal(markdown=current, search_terms=["compiler benchmarks"])

    jobs.enqueue(conn, uid, "public", "Use compiler benchmarks as my retrieval keywords", "terms", mode=mode)
    assert jobs.process_one(conn, Model())
    assert json.loads(profiles.head(conn, uid)["search_terms"]) == ["compiler benchmarks"]
