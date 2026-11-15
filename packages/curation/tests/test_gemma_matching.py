from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

import httpx
import pytest
from curator_core.config import clear_settings
from curator_curation.llm import gemma
from curator_curation.llm.schemas import NoteMatches, NotePostMatch


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
            futures = [pool.submit(models[index % 2].match_note, [], [], "") for index in range(8)]
            try:
                assert started.wait(5)
                assert peak == 2
            finally:
                release.set()
            assert all(not future.result(timeout=5).items for future in futures)
    assert peak == 2


@pytest.mark.parametrize("post_key,chunk_id", [("unknown", "chunk"), ("post", "unknown")])
def test_matching_rejects_ids_outside_the_owned_shortlist(conn, monkeypatch, post_key, chunk_id):
    result = NoteMatches(items=[NotePostMatch(post_key=post_key, chunk_id=chunk_id, relevance=0.9)])
    monkeypatch.setattr(gemma.GemmaClient, "_call", lambda *args, **kwargs: result)
    model = gemma.GemmaClient("https://validation.example/v1", "test", "test")
    with pytest.raises(ValueError, match="invalid note matches"):
        model.match_note(
            [{"post_key": "post", "text": "Original post"}], [{"chunk_id": "chunk", "text": "Original note"}], ""
        )
