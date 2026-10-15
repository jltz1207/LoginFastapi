"""處理一份已 claim 的文件：`PROCESSING` → `INDEXED` / `FAILED`。

順序刻意把「寫 Chroma」放在最後，緊接著就標 `INDEXED`，讓 chunk 已可檢索但 row 還不是
`INDEXED` 的時間只剩一次 DB commit，不用改任何 retrieval filter：

    download → load → raw_text / chunk → summary（失敗不擋）→ 存 raw_text、summary
      → 刪掉這份文件的舊 chunk → upsert chunk（deterministic id）→ INDEXED

租戶隔離：只接收 `document_id`。tenant / user / kb / storage key 一律從 DB 的 row 讀，
所有 Chroma 寫入和刪除都帶這三個 scope 加 `document_id`。

Idempotent：同一份文件重跑會先刪掉自己的 chunk 再用 `{document_id}:{index}` upsert，
所以不會有重複 chunk；summary 只有一個欄位，已經有值就跳過，不重複花 LLM 成本。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from langchain_core.documents import Document as LCDocument

from app.core.config import settings
from app.core.logging import get_logger
from app.core.storage.fileStorage import download_file
from app.ingestion.repository import DocumentRecord, IngestionRepository, SqlIngestionRepository
from app.ingestion.summarizer import DocumentSummarizer
from app.models.document import IngestionStatus
from app.rag.cleaning.data_cleaning import clean_chunks, clean_extracted_documents
from app.rag.loaders.base_loader import base_loader
from app.rag.splitters.chunkers import BaseChunker, RecursiveChunks
from app.utils.token_counter import count_tokens
from app.vectorstore import get_vector_store_indexer
from app.vectorstore.StoreIndexer import StoreIndexer

logger = get_logger(__name__)

ERROR_MESSAGE_MAX_CHARS = 2000


class IngestionError(Exception):
    """A failure with a message that is safe and useful to show in error_message."""


@dataclass
class IngestionDeps:
    repository: IngestionRepository = field(default_factory=SqlIngestionRepository)
    store_indexer: StoreIndexer = field(default_factory=get_vector_store_indexer)
    summarizer: DocumentSummarizer = field(default_factory=DocumentSummarizer)
    download_file: Callable[[str, str], bytes] = download_file
    load: Callable[[str, bytes], list[LCDocument] | None] = base_loader.load
    chunker: BaseChunker = field(default_factory=RecursiveChunks)
    count_tokens: Callable[[list[str]], int] = lambda texts: count_tokens(texts, settings.EMBEDDING_PROVIDE_TYPE)


def chunk_id(document_id: str, index: int) -> str:
    return f"{document_id}:{index}"


def _error_message(prefix: str, exc: Exception) -> str:
    detail = str(exc) if isinstance(exc, IngestionError) else f"{type(exc).__name__}: {exc}"
    return f"{prefix}{detail}"[:ERROR_MESSAGE_MAX_CHARS]


async def process_document(document_id: uuid.UUID, attempt: int, deps: IngestionDeps) -> IngestionStatus | None:
    """Returns the status written, or None if the row is gone or the lease was lost."""
    doc = await deps.repository.get(document_id)
    if doc is None:
        logger.warning("ingestion.document_missing", document_id=str(document_id))
        return None

    # Chroma metadata must be str (docs/id-type-convention-20260827.md).
    tenant_id, user_id = str(doc.tenant_id), str(doc.user_id)
    knowledge_base_id, doc_id = str(doc.knowledge_base_id), str(doc.id)
    log = logger.bind(
        document_id=doc_id, tenant_id=tenant_id, user_id=user_id,
        knowledge_base_id=knowledge_base_id, attempt=attempt,
    )
    chroma_touched = False
    try:
        pages = await load_pages(doc, deps.download_file, deps.load)
        raw_text = build_raw_text(pages)

        chunks = clean_chunks(deps.chunker.chunk(pages))
        if not chunks:
            raise IngestionError("file produced no chunks")
        token_count = deps.count_tokens([c.page_content for c in chunks])

        summary, summary_error = doc.summary, None
        if summary is None:
            try:
                summary = await deps.summarizer.summarize(raw_text)
            except Exception as exc:
                # Chunks stay usable without a summary; GLOBAL just skips this document.
                log.warning("ingestion.summary_failed", exc_info=True)
                summary_error = _error_message("summary failed: ", exc)

        if not await deps.repository.save_content(
            document_id, attempt, raw_text=raw_text, page_count=len(pages), summary=summary
        ):
            log.warning("ingestion.lease_lost", step="save_content")
            return None

        metadata = {
            "knowledge_base_id": knowledge_base_id,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "document_id": doc_id,
        }
        chroma_touched = True
        await asyncio.to_thread(
            deps.store_indexer.delete_document_chunks, tenant_id, user_id, knowledge_base_id, doc_id
        )
        await asyncio.to_thread(
            deps.store_indexer.add_documents,
            chunks,
            tenant_id=tenant_id,
            user_id=user_id,
            metadata=metadata,
            ids=[chunk_id(doc_id, i) for i in range(len(chunks))],
        )

        if not await deps.repository.mark_indexed(
            document_id, attempt, chunk_count=len(chunks), token_count=token_count, error_message=summary_error
        ):
            # Someone re-claimed the row; their run rewrites the same chunk ids.
            log.warning("ingestion.lease_lost", step="mark_indexed")
            return None
        log.info("ingestion.indexed", chunk_count=len(chunks), has_summary=summary is not None)
        return IngestionStatus.INDEXED

    except Exception as exc:
        log.exception("ingestion.failed")
        if chroma_touched:
            # A FAILED document must not stay searchable.
            try:
                await asyncio.to_thread(
                    deps.store_indexer.delete_document_chunks, tenant_id, user_id, knowledge_base_id, doc_id
                )
            except Exception:
                log.exception("ingestion.cleanup_failed")
        if not await deps.repository.mark_failed(document_id, attempt, _error_message("", exc)):
            log.warning("ingestion.lease_lost", step="mark_failed")
            return None
        return IngestionStatus.FAILED


async def load_pages(
    doc: DocumentRecord,
    download: Callable[[str, str], bytes],
    load: Callable[[str, bytes], list[LCDocument] | None],
) -> list[LCDocument]:
    file_bytes = await asyncio.to_thread(download, doc.storage_bucket, doc.storage_key)
    # Loaders parse (and, for md, write a temp file) synchronously: keep them off the loop.
    pages = await asyncio.to_thread(load, doc.file_extension, file_bytes)
    if pages is None:
        raise IngestionError(f"unsupported file type: {doc.file_extension}")
    if not pages:
        raise IngestionError("file has no pages")
    return pages


def build_raw_text(pages: list[LCDocument]) -> str:
    """documents.raw_text: the cleaned loader output, page by page.

    Not the chunks joined back together: chunk overlap would repeat text. Works on copies,
    because clean_extracted_documents mutates in place and the chunker still needs `pages`.
    """
    cleaned = clean_extracted_documents(
        [LCDocument(page_content=p.page_content, metadata=dict(p.metadata)) for p in pages]
    )
    if not cleaned:
        raise IngestionError("no text could be extracted from the file")
    return "\n\n".join(p.page_content for p in cleaned)
