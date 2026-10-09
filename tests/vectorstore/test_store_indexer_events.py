import asyncio
import gc
import threading
import time

import pytest
from langchain_core.documents import Document

from app.vectorstore import events
from app.vectorstore.StoreIndexer import StoreIndexer


@pytest.fixture(autouse=True)
def _isolated_listeners(monkeypatch):
    monkeypatch.setattr(events, "_listeners", [])


class _Recorder:
    """Bound-method listener (events holds listeners weakly, so keep a reference)."""

    def __init__(self, log: list[str]):
        self.log = log
        self.calls: list[tuple[str, str, str]] = []

    def on_changed(self, tenant_id, user_id, knowledge_base_id):
        self.calls.append((tenant_id, user_id, knowledge_base_id))
        self.log.append(f"notify:{knowledge_base_id}")


class _SlowCollection:
    def __init__(self, log: list[str], seconds: float, fail: bool):
        self.log, self.seconds, self.fail = log, seconds, fail
        self.write_threads: list[int] = []

    def add_documents(self, docs):
        self.write_threads.append(threading.get_ident())
        self.log.append("write-start")
        time.sleep(self.seconds)  # stands in for the embedding API call + SQLite write
        self.log.append("write-end")
        if self.fail:
            raise RuntimeError("embedding API down")


class _Manager:
    def __init__(self, collection):
        self.collection = collection

    def get_or_create_collection(self, user_id, tenant_id):
        return self.collection


def _indexer(log, seconds=0.0, fail=False) -> tuple[StoreIndexer, _SlowCollection]:
    collection = _SlowCollection(log, seconds, fail)
    return StoreIndexer(collection_manager=_Manager(collection)), collection


def _docs(*kb_ids):
    return [Document(page_content=f"chunk {i}", metadata={"knowledge_base_id": kb}) for i, kb in enumerate(kb_ids)]


def test_notifies_after_the_write_once_per_kb():
    log: list[str] = []
    recorder = _Recorder(log)
    events.on_kb_changed(recorder.on_changed)
    indexer, _ = _indexer(log)

    indexer.add_documents(_docs("kb1", "kb1", "kb2"), tenant_id="t", user_id="u")

    assert log == ["write-start", "write-end", "notify:kb1", "notify:kb2"]
    assert recorder.calls == [("t", "u", "kb1"), ("t", "u", "kb2")]


def test_caller_metadata_supplies_the_kb_id():
    log: list[str] = []
    recorder = _Recorder(log)
    events.on_kb_changed(recorder.on_changed)
    indexer, _ = _indexer(log)

    indexer.add_documents([Document(page_content="x")], tenant_id="t", user_id="u", metadata={"knowledge_base_id": "kb"})

    assert recorder.calls == [("t", "u", "kb")]


def test_notifies_even_if_the_write_fails():
    log: list[str] = []
    recorder = _Recorder(log)
    events.on_kb_changed(recorder.on_changed)
    indexer, _ = _indexer(log, fail=True)

    with pytest.raises(RuntimeError):
        indexer.add_documents(_docs("kb"), tenant_id="t", user_id="u")

    assert log == ["write-start", "write-end", "notify:kb"]


def test_failing_listener_does_not_fail_the_write_or_other_listeners():
    log: list[str] = []

    class _Broken:
        def on_changed(self, *args):
            raise ValueError("cache bug")

    broken, recorder = _Broken(), _Recorder(log)
    events.on_kb_changed(broken.on_changed)
    events.on_kb_changed(recorder.on_changed)
    indexer, _ = _indexer(log)

    indexer.add_documents(_docs("kb"), tenant_id="t", user_id="u")

    assert recorder.calls == [("t", "u", "kb")]


def test_garbage_collected_listener_is_dropped():
    log: list[str] = []
    recorder = _Recorder(log)
    events.on_kb_changed(recorder.on_changed)
    del recorder
    gc.collect()

    events.notify_kb_changed("t", "u", "kb")

    assert log == []
    assert events._listeners == []


def test_write_off_the_event_loop_does_not_block_it():
    log: list[str] = []
    indexer, collection = _indexer(log, seconds=0.3)

    async def run():
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        tick_task = asyncio.create_task(ticker())
        await asyncio.to_thread(indexer.add_documents, _docs("kb"), tenant_id="t", user_id="u")
        tick_task.cancel()
        return threading.get_ident(), ticks

    loop_thread, ticks = asyncio.run(run())

    assert collection.write_threads[0] != loop_thread
    # 0.3 s write / 0.01 s tick: a blocked loop would get ~0 ticks.
    assert ticks >= 10


def test_cancelled_request_still_notifies_only_after_the_write_finishes():
    log: list[str] = []
    recorder = _Recorder(log)
    events.on_kb_changed(recorder.on_changed)
    indexer, _ = _indexer(log, seconds=0.3)

    async def run():
        task = asyncio.create_task(
            asyncio.to_thread(indexer.add_documents, _docs("kb"), tenant_id="t", user_id="u")
        )
        await asyncio.sleep(0.05)  # write is in progress
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # The request is gone, but the worker thread is still writing.
        assert "notify:kb" not in log
        await asyncio.sleep(0.5)

    asyncio.run(run())

    assert log == ["write-start", "write-end", "notify:kb"]
