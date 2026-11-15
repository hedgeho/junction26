from types import SimpleNamespace

import pytest
from curator_curation import worker


@pytest.mark.parametrize("budget", [0, 1, 3])
def test_note_profile_backlog_is_bounded_before_matching(monkeypatch, budget):
    calls = []
    monkeypatch.setattr(worker, "settings", lambda: SimpleNamespace(note_profile_jobs_per_cycle=budget))
    monkeypatch.setattr(worker.jobs, "process_one", lambda *args, **kwargs: calls.append(kwargs) or True)
    worker.process_notes(None, object(), [])
    assert calls == [{"notes_only": True}] * budget


def test_warmup_failure_logs_no_provider_payload(monkeypatch, caplog):
    cfg = SimpleNamespace(
        gemma_url="https://warm.example/v1",
        gemma_token="private-token",
        gemma_model="test",
        inference_timeout_s=10,
        scoring_backend="gemma",
    )
    monkeypatch.setattr(worker, "settings", lambda: cfg)

    def fail(*args, **kwargs):
        raise ValueError("private source text and private-token")

    monkeypatch.setattr(worker, "GemmaClient", fail)
    worker._warm_inference()
    assert "warm-up unavailable" in caplog.text
    assert "private source" not in caplog.text and "private-token" not in caplog.text


def test_startup_queues_warmup_without_waiting_before_feed_cycle(monkeypatch):
    events = []
    cfg = SimpleNamespace(gemma_warmup=True, gemma_url="https://warm.example/v1", gemma_token="test")
    monkeypatch.setattr(worker, "settings", lambda: cfg)
    monkeypatch.setattr(worker.db, "connect", lambda: None)
    monkeypatch.setattr(worker, "pending_work", lambda conn: set())
    monkeypatch.setattr(worker, "_warmup_executor", SimpleNamespace(submit=lambda fn: events.append("queued")))

    class CycleReached(Exception):
        pass

    def cycle(conn, **kwargs):
        events.append("feed")
        assert kwargs == {"defer_notes": True}
        raise CycleReached()

    monkeypatch.setattr(worker, "run_cycle", cycle)
    with pytest.raises(CycleReached):
        worker.run_forever()
    assert events == ["queued", "feed"]
