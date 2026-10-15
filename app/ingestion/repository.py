"""`documents` table 當 ingestion queue。

狀態本身就是 queue：`PENDING` 等人撈、`PROCESSING` 處理中。

- **Claim**：`FOR UPDATE SKIP LOCKED` 讓多個 worker（例如多個 uvicorn worker）同時撈也不會拿到
  同一筆；claim 時 `attempt_count + 1`、記下 `processing_started_at`（lease 開始）。
- **Fencing**：之後每一次寫入都帶 `status = PROCESSING AND attempt_count = <claim 時的值>`。
  Lease 過期被別人重新 claim 之後，舊 worker 的寫入會落空（回傳 False），不會蓋掉新結果。
- **Lease 過期**：worker 中途死掉，row 會停在 `PROCESSING`。超過 lease 就退回 `PENDING`；
  已經試滿 `max_attempts` 次就改成 `FAILED`。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import AsyncSessionLocal
from app.models.document import Document, IngestionStatus

LEASE_EXPIRED_MESSAGE = "ingestion abandoned: worker lease expired"


@dataclass(frozen=True)
class DocumentRecord:
    """What the worker may know about a document: read from the row, never from the task."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    knowledge_base_id: uuid.UUID
    file_extension: str
    storage_bucket: str
    storage_key: str
    summary: str | None


class IngestionRepository(Protocol):
    async def claim_next(self) -> tuple[uuid.UUID, int] | None: ...
    async def requeue_stale(self, lease_seconds: int, max_attempts: int) -> int: ...
    async def get(self, document_id: uuid.UUID) -> DocumentRecord | None: ...
    async def save_content(
        self, document_id: uuid.UUID, attempt: int, *, raw_text: str, page_count: int, summary: str | None
    ) -> bool: ...
    async def mark_indexed(
        self, document_id: uuid.UUID, attempt: int, *, chunk_count: int, token_count: int, error_message: str | None
    ) -> bool: ...
    async def mark_failed(self, document_id: uuid.UUID, attempt: int, error_message: str) -> bool: ...


def claim_next_statement():
    next_pending = (
        select(Document.id)
        .where(Document.status == IngestionStatus.PENDING, Document.deleted_at.is_(None))
        .order_by(Document.created_dt)
        .limit(1)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    return (
        update(Document)
        .where(Document.id == next_pending)
        .values(
            status=IngestionStatus.PROCESSING,
            attempt_count=Document.attempt_count + 1,
            processing_started_at=func.now(),
            error_message=None,
        )
        .returning(Document.id, Document.attempt_count)
    )


def _fenced(document_id: uuid.UUID, attempt: int):
    return (
        Document.id == document_id,
        Document.status == IngestionStatus.PROCESSING,
        Document.attempt_count == attempt,
    )


class SqlIngestionRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None):
        self._session_factory = session_factory or AsyncSessionLocal

    async def claim_next(self) -> tuple[uuid.UUID, int] | None:
        async with self._session_factory() as db:
            row = (await db.execute(claim_next_statement())).first()
            await db.commit()
        return (row.id, row.attempt_count) if row else None

    async def requeue_stale(self, lease_seconds: int, max_attempts: int) -> int:
        stale = (
            Document.status == IngestionStatus.PROCESSING,
            Document.processing_started_at < func.now() - timedelta(seconds=lease_seconds),
        )
        async with self._session_factory() as db:
            failed = await db.execute(
                update(Document)
                .where(*stale, Document.attempt_count >= max_attempts)
                .values(status=IngestionStatus.FAILED, error_message=LEASE_EXPIRED_MESSAGE)
            )
            requeued = await db.execute(
                update(Document)
                .where(*stale, Document.attempt_count < max_attempts)
                .values(status=IngestionStatus.PENDING, processing_started_at=None)
            )
            await db.commit()
        return failed.rowcount + requeued.rowcount

    async def get(self, document_id: uuid.UUID) -> DocumentRecord | None:
        async with self._session_factory() as db:
            doc = (
                await db.execute(select(Document).where(Document.id == document_id, Document.deleted_at.is_(None)))
            ).scalars().first()
        if doc is None:
            return None
        return DocumentRecord(
            id=doc.id,
            tenant_id=doc.tenant_id,
            user_id=doc.user_id,
            knowledge_base_id=doc.knowledge_base_id,
            file_extension=doc.file_extension,
            storage_bucket=doc.storage_bucket,
            storage_key=doc.storage_key,
            summary=doc.summary,
        )

    async def save_content(
        self, document_id: uuid.UUID, attempt: int, *, raw_text: str, page_count: int, summary: str | None
    ) -> bool:
        values = {"raw_text": raw_text, "page_count": page_count}
        if summary is not None:
            values["summary"] = summary
        return await self._fenced_update(document_id, attempt, values)

    async def mark_indexed(
        self, document_id: uuid.UUID, attempt: int, *, chunk_count: int, token_count: int, error_message: str | None
    ) -> bool:
        return await self._fenced_update(document_id, attempt, {
            "status": IngestionStatus.INDEXED,
            "chunk_count": chunk_count,
            "token_count": token_count,
            "error_message": error_message,
            "indexed_at": func.now(),
            "processing_started_at": None,
        })

    async def mark_failed(self, document_id: uuid.UUID, attempt: int, error_message: str) -> bool:
        return await self._fenced_update(document_id, attempt, {
            "status": IngestionStatus.FAILED,
            "error_message": error_message,
            "processing_started_at": None,
        })

    async def _fenced_update(self, document_id: uuid.UUID, attempt: int, values: dict) -> bool:
        async with self._session_factory() as db:
            result = await db.execute(update(Document).where(*_fenced(document_id, attempt)).values(**values))
            await db.commit()
        return result.rowcount == 1
