from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

import httpx
import pytest
from curator_core.config import clear_settings
from curator_curation.llm import gemma


def test_all_clients_share_bounded_endpoint_capacity(conn, monkeypatch):
    monkeypatch.setenv("GEMMA_CONCURRENCY", "2")
    clear_settings()
    started, release = Event(), Event()
    lock = Lock()
    active = peak = 0

    def handle(request):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            if active == 2:
                started.set()
        try:
            assert release.wait(5)
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"items":[]}'}}]})
        finally:
            with lock:
                active -= 1

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(gemma, "_http_client", lambda timeout: client)
        models = [gemma.GemmaClient("https://bounded.example/v1", "test", "test") for _ in range(2)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [
                pool.submit(
                    models[index % 2].match_note,
                    [{"post_key": "post", "text": "post"}],
                    [{"chunk_id": "chunk", "text": "note"}],
                    "",
                )
                for index in range(8)
            ]
            try:
                assert started.wait(5)
                assert peak == 2
            finally:
                release.set()
            assert all(not future.result(timeout=5).items for future in futures)
    assert peak == 2


@pytest.mark.parametrize("post_index,chunk_index", [(1, 0), (0, 1)])
def test_matching_rejects_ids_outside_the_owned_shortlist(conn, monkeypatch, post_index, chunk_index):
    result = gemma.IndexedNoteMatches(items=[gemma.IndexedNoteMatch(p=post_index, c=chunk_index, r=0.9)])
    monkeypatch.setattr(gemma.GemmaClient, "_call", lambda *args, **kwargs: result)
    model = gemma.GemmaClient("https://validation.example/v1", "test", "test")
    with pytest.raises(ValueError, match="invalid note matches"):
        model.match_note(
            [{"post_key": "post", "text": "Original post"}], [{"chunk_id": "chunk", "text": "Original note"}], ""
        )


def test_note_matching_uses_small_indices_and_maps_back_to_owned_sources(conn, monkeypatch):
    payloads = []

    def call(self, instruction, payload, schema, max_tokens):
        payloads.append(payload)
        assert max_tokens == 80
        return schema(items=[gemma.IndexedNoteMatch(p=0, c=0, r=0.9)])

    monkeypatch.setattr(gemma.GemmaClient, "_call", call)
    model = gemma.GemmaClient("https://compact.example/v1", "test", "test")
    result = model.match_note(
        [{"post_key": "bluesky:long-owned-uri", "text": "Original public post"}],
        [{"chunk_id": "private-chunk-id", "title": "My note", "text": "Original private passage"}],
        "Private profile",
    )
    assert result.items[0].post_key == "bluesky:long-owned-uri"
    assert result.items[0].chunk_id == "private-chunk-id"
    assert "long-owned-uri" not in str(payloads) and "private-chunk-id" not in str(payloads)
    assert payloads[0]["chunks"][0]["text"] == "Original private passage"


def test_empty_matching_inputs_make_no_inference_call(conn, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("Empty input must not call the model")

    monkeypatch.setattr(gemma.GemmaClient, "_call", unexpected)
    model = gemma.GemmaClient("https://empty.example/v1", "test", "test")
    assert not model.match_note([], [{"chunk_id": "chunk", "text": "note"}], "").items
    assert not model.match_note([{"post_key": "post", "text": "post"}], [], "").items


def test_truncated_inference_is_not_accepted_even_when_json_parses(conn, monkeypatch):
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"choices": [{"finish_reason": "length", "message": {"content": '{"items":[]}'}}]}
            )
        )
    ) as client:
        monkeypatch.setattr(gemma, "_http_client", lambda timeout: client)
        model = gemma.GemmaClient("https://truncated.example/v1", "test", "test")
        with pytest.raises(ValueError, match="token budget"):
            model.match_note([{"post_key": "post", "text": "post"}], [{"chunk_id": "chunk", "text": "note"}], "")


def test_warmup_prepares_only_fixed_schemas_with_empty_synthetic_payloads(conn, monkeypatch):
    calls = []

    def call(self, instruction, payload, schema, max_tokens):
        import json

        assert payload == {}
        expected = {
            name: [] if field.get("type") == "array" else None
            for name, field in schema.model_json_schema()["properties"].items()
        }
        assert json.loads(instruction.split(": ", 1)[1]) == expected
        assert max_tokens == 256
        calls.append(schema.__name__)
        return schema.model_validate(expected)

    monkeypatch.setattr(gemma.GemmaClient, "_call", call)
    model = gemma.GemmaClient("https://warm.example/v1", "test", "test")
    model.warmup(scoring=True)
    assert calls == ["ProfileEdits", "IndexedNoteMatches", "FeedSelection", "Assessments"]
