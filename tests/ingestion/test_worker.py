"""IngestionWorker loop and the SQL the queue relies on."""

import asyncio
import uuid

from sqlalchemy.dialects import postgresql

from app.ingestion.repository import LEASE_EXPIRED_MESSAGE, SqlIngestionRepository, claim_next_statement
from app.ingestion.worker import IngestionWorker
from app.models.document import IngestionStatus

from ingestion_fakes import FakeRepository, make_record


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def test_run_once_requeues_then_claims_and_processes():
    record = make_record()
    repo = FakeRepository(record)
    processed = []

    async def process(document_id, attempt):
        processed.append((document_id, attempt))

    worker = IngestionWorker(repo, process, poll_interval=60, lease_seconds=900, max_attempts=3)

    assert asyncio.run(worker.run_once()) is True
    assert processed == [(record.id, 1)]
    assert repo.requeue_calls == [(900, 3)]
    assert asyncio.run(worker.run_once()) is False  # queue empty


def test_notify_wakes_the_loop_without_waiting_for_poll():
    repo = FakeRepository()
    processed = asyncio.Queue()

    async def process(document_id, attempt):
        processed.put_nowait(document_id)

    async def go():
        worker = IngestionWorker(repo, process, poll_interval=60)
        worker.start()
        await asyncio.sleep(0.05)  # loop is idle, sleeping up to 60s
        record = make_record()
        repo.add(record)
        worker.notify()
        got = await asyncio.wait_for(processed.get(), timeout=2)
        await worker.stop()
        return record.id, got

    expected, got = asyncio.run(go())
    assert got == expected


def test_loop_survives_a_failing_iteration():
    class FlakyRepository(FakeRepository):
        def __init__(self, *records):
            super().__init__(*records)
            self.failures = 1

        async def claim_next(self):
            if self.failures:
                self.failures -= 1
                raise ConnectionError("db down")
            return await super().claim_next()

    record = make_record()
    repo = FlakyRepository(record)
    processed = asyncio.Queue()

    async def process(document_id, attempt):
        processed.put_nowait(document_id)

    async def go():
        worker = IngestionWorker(repo, process, poll_interval=0.01)
        worker.start()
        got = await asyncio.wait_for(processed.get(), timeout=2)
        await worker.stop()
        return got

    assert asyncio.run(go()) == record.id


def test_claim_uses_skip_locked_and_bumps_attempt():
    sql = _sql(claim_next_statement())

    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "documents.status = 'PENDING'" in sql
    assert "documents.deleted_at IS NULL" in sql
    assert "ORDER BY documents.created_dt" in sql
    assert "status='PROCESSING'" in sql
    assert "attempt_count=(documents.attempt_count + 1)" in sql
    assert "RETURNING documents.id, documents.attempt_count" in sql


class _RecordingSession:
    def __init__(self, log):
        self._log = log

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        self._log.append(stmt)

        class _Result:
            rowcount = 1

        return _Result()

    async def commit(self):
        pass


def _recording_repository():
    log = []
    return SqlIngestionRepository(session_factory=lambda: _RecordingSession(log)), log


def test_status_writes_are_fenced_by_attempt():
    repo, log = _recording_repository()
    doc_id = uuid.uuid4()

    asyncio.run(repo.mark_indexed(doc_id, 2, chunk_count=3, token_count=10, error_message=None))
    asyncio.run(repo.mark_failed(doc_id, 2, "boom"))
    asyncio.run(repo.save_content(doc_id, 2, raw_text="t", page_count=1, summary=None))

    for stmt in log:
        sql = _sql(stmt)
        assert f"documents.id = '{doc_id}'" in sql
        assert "documents.status = 'PROCESSING'" in sql
        assert "documents.attempt_count = 2" in sql
    assert "summary" not in _sql(log[2])  # None leaves an existing summary alone


def test_requeue_stale_fails_rows_out_of_attempts():
    repo, log = _recording_repository()

    asyncio.run(repo.requeue_stale(lease_seconds=900, max_attempts=3))

    failed, requeued = (_sql(s) for s in log)
    for sql in (failed, requeued):
        assert "documents.status = 'PROCESSING'" in sql
        assert "documents.processing_started_at < now() -" in sql
    assert "documents.attempt_count >= 3" in failed and f"'{LEASE_EXPIRED_MESSAGE}'" in failed
    assert f"status='{IngestionStatus.FAILED.value}'" in failed
    assert "documents.attempt_count < 3" in requeued and "status='PENDING'" in requeued
