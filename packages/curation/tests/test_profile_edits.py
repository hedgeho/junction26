import pytest
from curator_curation import profile_edits
from curator_curation.llm.schemas import PreferenceLine, ProfileEdits

FLAT = (
    "# Social filtering profile  ## Goals Green birds.  ## Existing preferences "
    "- [topic] More: posts about birds that are green, especially bird photos. "
    "- [topic] More: all animals - [rule] Exclude: furries - [topic] More: politics "
    "- [search_term] green bird"
)


def test_flattened_profile_parses_into_lines():
    head, items, tail = profile_edits.parse(FLAT)
    assert head == "# Social filtering profile  ## Goals Green birds.  ## Existing preferences"
    assert items[1:4] == [("topic", "More: all animals"), ("rule", "Exclude: furries"), ("topic", "More: politics")]
    assert tail == ""


def test_edits_change_only_the_named_lines_and_keep_terms():
    edits = ProfileEdits(
        remove=[3],
        add=[PreferenceLine(category="topic", text="More: politics, but not US politics")],
        add_terms=["elections"],
        remove_terms=["Politics"],
    )
    markdown, terms = profile_edits.apply(FLAT, ["green bird", "politics", "birds"], edits)
    assert "- [rule] Exclude: furries\n" in markdown
    assert "- [topic] More: politics\n" not in markdown
    assert markdown.rstrip().endswith("- [topic] More: politics, but not US politics")
    assert "\n\n## Goals Green birds." in markdown
    assert terms == ["green bird", "birds", "elections"]


def test_empty_edits_keep_content_and_unknown_ids_are_rejected():
    markdown, terms = profile_edits.apply(FLAT, ["birds"], ProfileEdits())
    assert profile_edits.parse(markdown)[1] == profile_edits.parse(FLAT)[1] and terms == ["birds"]
    with pytest.raises(ValueError):
        profile_edits.apply(FLAT, [], ProfileEdits(remove=[99]))


def test_multiline_profile_and_trailing_section_survive():
    doc = "# Interests\n\nCompilers.\n\n- [topic] More: register allocation\n- [rule] No hype\n\n## Notes\nKeep this."
    markdown, _ = profile_edits.apply(
        doc, [], ProfileEdits(add=[PreferenceLine(category="topic", text="More: GPU scheduling")])
    )
    assert markdown == (
        "# Interests\n\nCompilers.\n\n- [topic] More: register allocation\n- [rule] No hype\n"
        "- [topic] More: GPU scheduling\n\n## Notes\nKeep this.\n"
    )


def test_gemma_sees_numbered_lines_and_terms_not_a_document(monkeypatch):
    from curator_curation.llm.gemma import GemmaClient

    seen = {}

    def call(self, instruction, payload, schema):
        seen.update(payload)
        return ProfileEdits(add_terms=["kingfisher"])

    monkeypatch.setattr(GemmaClient, "_call", call)
    result = GemmaClient("https://gemma.example/v1", "t", "m").update_profile(FLAT, "more kingfishers", ["birds"])
    assert seen["preferences"][2] == {"id": 2, "category": "rule", "text": "Exclude: furries"}
    assert seen["search_terms"] == ["birds"] and "current_markdown" not in seen
    assert result.search_terms == ["birds", "kingfisher"]
