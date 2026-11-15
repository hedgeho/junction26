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


@pytest.mark.parametrize(
    "text",
    [
        "I want more of this",
        "More like this",
        "Less of these posts please",
        "Prefer content similar to this.",
        "More please",
    ],
)
def test_vague_references_without_source_context_do_not_call_the_model(monkeypatch, text):
    from curator_curation.llm.gemma import GemmaClient

    def unexpected_call(*args, **kwargs):
        pytest.fail("Unresolvable feedback must not trigger model inference")

    monkeypatch.setattr(GemmaClient, "_call", unexpected_call)
    result = GemmaClient("https://gemma.example/v1", "t", "m").update_profile(FLAT, text, ["birds"])
    assert result.markdown == FLAT and result.search_terms == ["birds"]


@pytest.mark.parametrize(
    "text", ["More: compiler benchmarks", "Prefer technical posts like this", "Less promotional hype"]
)
def test_explicit_preferences_are_not_mistaken_for_empty_references(text):
    assert not profile_edits.unresolved_reference(text)


@pytest.mark.parametrize("category,text", [("topic", "More like this"), ("feedback", "Less hype")])
def test_model_cannot_save_transcripts_or_unresolved_preferences(monkeypatch, category, text):
    from curator_curation.llm.gemma import GemmaClient

    def call(*args, **kwargs):
        return ProfileEdits(add=[PreferenceLine(category=category, text=text)])

    monkeypatch.setattr(GemmaClient, "_call", call)
    model = GemmaClient("https://gemma.example/v1", "t", "m")
    with pytest.raises(ValueError, match="standalone curation principles"):
        model.update_profile(FLAT, "<post>A compiler benchmark</post><user_feedback>More of this</user_feedback>")


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


def test_gemma_sees_full_document_as_well_as_numbered_lines_and_terms(monkeypatch):
    from curator_curation.llm.gemma import GemmaClient

    seen = {}

    def call(self, instruction, payload, schema, max_tokens=8192):
        seen.update(payload)
        return ProfileEdits(add_terms=["kingfisher"])

    monkeypatch.setattr(GemmaClient, "_call", call)
    result = GemmaClient("https://gemma.example/v1", "t", "m").update_profile(FLAT, "more kingfishers", ["birds"])
    assert seen["preferences"][2] == {"id": 2, "category": "rule", "text": "Exclude: furries"}
    assert seen["search_terms"] == ["birds"] and seen["current_markdown"] == FLAT
    assert result.search_terms == ["birds", "kingfisher"]


def test_bare_removals_delete_without_requiring_an_addition():
    markdown, _ = profile_edits.apply(FLAT, [], ProfileEdits(remove=[2]))
    assert "Exclude: furries" not in markdown
    assert "More: politics" in markdown


def test_narrow_contextual_feedback_preserves_unrelated_interests(monkeypatch):
    from curator_curation.llm.gemma import GemmaClient

    original = "# Profile\n\n- [topic] Prefer in-depth compiler evaluations with reproducible benchmarks\n"

    def call(self, instruction, payload, schema, max_tokens):
        assert "keep unrelated interests" in instruction
        return ProfileEdits(add=[PreferenceLine(category="rule", text="Exclude marketing announcements")])

    monkeypatch.setattr(GemmaClient, "_call", call)
    result = GemmaClient("https://gemma.example/v1", "t", "m").update_profile(
        original, "<post>A marketing announcement</post><user_feedback>Less of this</user_feedback>"
    )
    assert "- [topic] Prefer in-depth compiler evaluations with reproducible benchmarks" in result.markdown
    assert "- [rule] Exclude marketing announcements" in result.markdown
    assert "[history]" not in result.markdown


def test_contextual_feedback_can_still_update_the_same_subject(monkeypatch):
    from curator_curation.llm.gemma import GemmaClient

    original = "# Profile\n\n- [topic] More: cooking\n"

    def call(*args, **kwargs):
        return ProfileEdits(
            remove=[0], add=[PreferenceLine(category="topic", text="More: cooking, but not Italian food")]
        )

    monkeypatch.setattr(GemmaClient, "_call", call)
    result = GemmaClient("https://gemma.example/v1", "t", "m").update_profile(
        original, "<post>Italian cooking</post><user_feedback>Not Italian food</user_feedback>"
    )
    assert "Previously: More: cooking" not in result.markdown
    assert "- [topic] More: cooking, but not Italian food" in result.markdown


def test_legacy_search_term_lines_are_hidden_from_the_model_and_kept():
    _, shown = profile_edits.numbered(FLAT)
    assert all(item["category"] != "search_term" for item in shown) and len(shown) == 4
    edits = ProfileEdits(
        remove=[3, 4],
        add=[
            PreferenceLine(category="topic", text="Much more: politics"),
            PreferenceLine(category="search_term", text="elections"),
        ],
    )
    markdown, terms = profile_edits.apply(FLAT, ["politics"], edits)
    assert "- [search_term] green bird" in markdown and "- [search_term] elections" not in markdown
    assert "- [topic] Much more: politics" in markdown and "- [topic] More: politics\n" not in markdown
    assert terms == ["politics", "elections"]


def test_replaced_topics_are_not_automatically_retained_as_history():
    old = "# Feed\n\n- [topic] More: compiler research\n"
    edits = ProfileEdits(remove=[0], add=[PreferenceLine(category="topic", text="Current: programming languages")])
    markdown, _ = profile_edits.apply(old, [], edits)
    assert "compiler research" not in markdown
    assert "[topic] Current: programming languages" in markdown
