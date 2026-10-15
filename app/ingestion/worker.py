"""在 API process 裡跑的 ingestion worker。

為什麼不拆成獨立 process：BM25 cache 的失效通知（`app/vectorstore/events.py`）只在同一個
process 內有效，Chroma 也是本機 `PersistentClient`。Worker 和 API 同 process，這兩件事都維持
現狀。Durability 由 `documents` table 提供（見 `repository.py`），process 重啟後會重新撈。

Upload 寫完 `PENDING` 後呼叫 `notify_ingestion_worker()` 立刻叫醒 loop；polling 只是保底
（例如別的 uvicorn worker 收到的 upload、或 lease 過期被退回的文件）。

之後要拆成獨立 process 或換 Celery 時，`process_document()` 不用改，只換這一層。
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

from app.core.config import settings
from app.core.logging import get_logger
from app.ingestion.processor import IngestionDeps, process_document
from app.ingestion.repository import IngestionRepository

logger = get_logger(__name__)

ProcessFn = Callable[[uuid.UUID, int], Awaitable[object]]


class IngestionWorker:
    def __init__(
        self,
        repository: IngestionRepository,
        process: ProcessFn,
        *,
        poll_interval: float | None = None,
        lease_seconds: int | None = None,
        max_attempts: int | None = None,
    ):
        self._repository = repository
        self._process = process
        self._poll_interval = poll_interval if poll_interval is not None else settings.INGESTION_POLL_INTERVAL_SECONDS
        self._lease_seconds = lease_seconds if lease_seconds is not None else settings.INGESTION_LEASE_SECONDS
        self._max_attempts = max_attempts if max_attempts is not None else settings.INGESTION_MAX_ATTEMPTS
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None

    def notify(self) -> None:
        self._wake.set()

    async def run_once(self) -> bool:
        """Requeue abandoned rows, then claim and process one document. True if one was claimed."""
        recovered = await self._repository.requeue_stale(self._lease_seconds, self._max_attempts)
        if recovered:
            logger.warning("ingestion.lease_expired", count=recovered)
        claimed = await self._repository.claim_next()
        if claimed is None:
            return False
        document_id, attempt = claimed
        await self._process(document_id, attempt)
        return True

    async def run_forever(self) -> None:
        while True:
            # Cleared before looking at the queue, so a notify() that lands while
            # run_once() is busy is not lost.
            self._wake.clear()
            try:
                if await self.run_once():
                    continue  # drain the queue before sleeping
            except asyncio.CancelledError:
                raise
            except Exception:
                # e.g. DB unreachable: back off, never let the loop die.
                logger.exception("ingestion.worker_iteration_failed")
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self._poll_interval)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.run_forever(), name="ingestion-worker")

    async def stop(self) -> None:
        # A document cut off mid-run stays PROCESSING and is requeued once its lease expires.
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None


_worker: IngestionWorker | None = None


def notify_ingestion_worker() -> None:
    """Wake the worker after a new PENDING row is committed. No-op if it is not running."""
    if _worker is not None:
        _worker.notify()


def build_ingestion_worker() -> IngestionWorker:
    deps = IngestionDeps()

    async def process(document_id: uuid.UUID, attempt: int) -> None:
        await process_document(document_id, attempt, deps)

    return IngestionWorker(deps.repository, process)


@asynccontextmanager
async def ingestion_worker_lifespan():
    global _worker
    if not settings.INGESTION_WORKER_ENABLED:
        logger.info("ingestion.worker_disabled")
        yield
        return
    _worker = build_ingestion_worker()
    _worker.start()
    logger.info("ingestion.worker_started")
    try:
        yield
    finally:
        await _worker.stop()
        _worker = None
        logger.info("ingestion.worker_stopped")
