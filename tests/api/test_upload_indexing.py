import asyncio
import threading
import time

import pytest

from app.api.v1.endpoints import knowledgeBases
from app.api.v1.endpoints.knowledgeBases import _write_chunks_and_invalidate_bm25

_METADATA = {"tenant_id": "t", "user_id": "u", "knowledge_base_id": "kb"}


class _RecordingProvider:
    def __init__(self, events: list[str]):
        self.events = events
        self.keys: list[tuple[str, str, str]] = []

    def invalidate(self, tenant_id, user_id, knowledge_base_id):
        self.keys.append((tenant_id, user_id, knowledge_base_id))
        self.events.append("invalidate")


class _SlowStore:
    def __init__(self, events: list[str], seconds: float = 0.3, fail: bool = False):
        self.events = events
        self.seconds = seconds
        self.fail = fail
        self.write_threads: list[int] = []

    def add_documents(self, docs, tenant_id, user_id, metadata=None):
        self.write_threads.append(threading.get_ident())
        self.events.append("write-start")
        time.sleep(self.seconds)  # stands in for the embedding API call + SQLite write
        self.events.append("write-end")
        if self.fail:
            raise RuntimeError("embedding API down")


@pytest.fixture
def events(monkeypatch) -> list[str]:
    events: list[str] = []
    provider = _RecordingProvider(events)
    monkeypatch.setattr(knowledgeBases, "get_bm25_index_provider", lambda: provider)
    return events


def test_write_does_not_block_the_event_loop(events):
    store = _SlowStore(events)

    async def run():
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        tick_task = asyncio.create_task(ticker())
        await asyncio.to_thread(_write_chunks_and_invalidate_bm25, store, [], _METADATA)
        tick_task.cancel()
        return threading.get_ident(), ticks

    loop_thread, ticks = asyncio.run(run())

    assert store.write_threads[0] != loop_thread
    # 0.3 s write / 0.01 s tick: a blocked loop would get ~0 ticks.
    assert ticks >= 10


def test_invalidate_runs_after_write(events):
    _write_chunks_and_invalidate_bm25(_SlowStore(events, seconds=0), [], _METADATA)

    assert events == ["write-start", "write-end", "invalidate"]


def test_invalidate_runs_even_if_write_fails(events):
    with pytest.raises(RuntimeError):
        _write_chunks_and_invalidate_bm25(_SlowStore(events, seconds=0, fail=True), [], _METADATA)

    assert events == ["write-start", "write-end", "invalidate"]


def test_cancelled_request_still_invalidates_after_write_finishes(events):
    store = _SlowStore(events, seconds=0.3)

    async def run():
        task = asyncio.create_task(
            asyncio.to_thread(_write_chunks_and_invalidate_bm25, store, [], _METADATA)
        )
        await asyncio.sleep(0.05)  # write is in progress
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # The request is gone, but the worker thread is still writing.
        assert "invalidate" not in events
        await asyncio.sleep(0.5)

    asyncio.run(run())

    assert events == ["write-start", "write-end", "invalidate"]
