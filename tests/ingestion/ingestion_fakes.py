"""Fakes shared by tests/ingestion: no DB, no LLM, no embedding API.

- FakeRepository keeps rows in memory and enforces the same fencing as the SQL one
  (writes only land while status == PROCESSING and attempt_count matches).
- Chroma is a real in-memory collection with a fake embedding, shared by every
  (user, tenant), i.e. PARTITION_METHOD=1: the worst case, where the metadata filter
  is the only isolation left.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace

import chromadb
from langchain_chroma import Chroma
from langchain_core.documents import Document as LCDocument
from langchain_core.embeddings import DeterministicFakeEmbedding

from app.ingestion.processor import IngestionDeps
from app.ingestion.repository import DocumentRecord
from app.models.document import IngestionStatus
from app.rag.splitters.chunkers import RecursiveChunks
from app.vectorstore.StoreIndexer import StoreIndexer


class SharedCollectionManager:
    def __init__(self):
        self.collection = Chroma(
            client=chromadb.EphemeralClient(),
            collection_name=f"test_{uuid.uuid4().hex}",
            embedding_function=DeterministicFakeEmbedding(size=8),
        )

    def get_or_create_collection(self, user_id: str, tenant_id: str) -> Chroma:
        return self.collection


def make_indexer() -> StoreIndexer:
    return StoreIndexer(collection_manager=SharedCollectionManager())


def all_records(indexer: StoreIndexer) -> list[tuple[str, dict]]:
    raw = indexer.collection_manager.collection.get(include=["metadatas"])
    return sorted(zip(raw["ids"], raw["metadatas"]), key=lambda r: r[0])


@dataclass
class Row:
    record: DocumentRecord
    status: IngestionStatus = IngestionStatus.PENDING
    attempt_count: int = 0
    raw_text: str | None = None
    page_count: int | None = None
    chunk_count: int | None = None
    token_count: int | None = None
    error_message: str | None = None
    indexed: bool = False


class FakeRepository:
    def __init__(self, *records: DocumentRecord):
        self.rows = {r.id: Row(record=r) for r in records}
        self.requeue_calls: list[tuple[int, int]] = []

    def add(self, record: DocumentRecord) -> None:
        self.rows[record.id] = Row(record=record)

    async def claim_next(self):
        for doc_id, row in self.rows.items():
            if row.status == IngestionStatus.PENDING:
                row.status = IngestionStatus.PROCESSING
                row.attempt_count += 1
                row.error_message = None
                return doc_id, row.attempt_count
        return None

    async def requeue_stale(self, lease_seconds: int, max_attempts: int) -> int:
        self.requeue_calls.append((lease_seconds, max_attempts))
        return 0

    async def get(self, document_id):
        row = self.rows.get(document_id)
        return row.record if row else None

    def _fenced(self, document_id, attempt) -> Row | None:
        row = self.rows[document_id]
        if row.status == IngestionStatus.PROCESSING and row.attempt_count == attempt:
            return row
        return None

    async def save_content(self, document_id, attempt, *, raw_text, page_count, summary):
        row = self._fenced(document_id, attempt)
        if row is None:
            return False
        row.raw_text, row.page_count = raw_text, page_count
        if summary is not None:
            row.record = replace(row.record, summary=summary)
        return True

    async def mark_indexed(self, document_id, attempt, *, chunk_count, token_count, error_message):
        row = self._fenced(document_id, attempt)
        if row is None:
            return False
        row.status = IngestionStatus.INDEXED
        row.chunk_count, row.token_count, row.error_message = chunk_count, token_count, error_message
        row.indexed = True
        return True

    async def mark_failed(self, document_id, attempt, error_message):
        row = self._fenced(document_id, attempt)
        if row is None:
            return False
        row.status, row.error_message = IngestionStatus.FAILED, error_message
        return True


class FakeSummarizer:
    def __init__(self, error: Exception | None = None):
        self.calls: list[str] = []
        self._error = error

    async def summarize(self, text: str) -> str:
        self.calls.append(text)
        if self._error:
            raise self._error
        return f"summary of {len(text)} chars"


@dataclass
class FakeStorage:
    """storage_key → file text. The loader returns one page per blank-line-separated block."""

    files: dict[str, str] = field(default_factory=dict)
    loader_metadata: dict = field(default_factory=dict)

    def download(self, storage_bucket: str, storage_key: str) -> bytes:
        return self.files[storage_key].encode("utf-8")

    def load(self, file_ext: str, data: bytes):
        if file_ext not in ("pdf", "md"):
            return None
        return [
            LCDocument(page_content=page, metadata={"page": i, **self.loader_metadata})
            for i, page in enumerate(data.decode("utf-8").split("\n\n\n"))
        ]


def make_record(tenant="t1", user="u1", kb="kb1", storage_key="key", summary=None, doc_id=None) -> DocumentRecord:
    ns = uuid.NAMESPACE_URL
    return DocumentRecord(
        id=doc_id or uuid.uuid4(),
        tenant_id=uuid.uuid5(ns, tenant),
        user_id=uuid.uuid5(ns, user),
        knowledge_base_id=uuid.uuid5(ns, kb),
        file_extension="md",
        storage_bucket="bucket",
        storage_key=storage_key,
        summary=summary,
    )


def make_deps(repo, indexer=None, storage=None, summarizer=None) -> IngestionDeps:
    storage = storage or FakeStorage()
    return IngestionDeps(
        repository=repo,
        store_indexer=indexer or make_indexer(),
        summarizer=summarizer or FakeSummarizer(),
        download_file=storage.download,
        load=storage.load,
        chunker=RecursiveChunks(),
        count_tokens=lambda texts: sum(len(t) for t in texts),
    )


def words(n: int, prefix: str = "word") -> str:
    """~n * 8 characters of distinct words; RecursiveChunks cuts at 500 characters."""
    return " ".join(f"{prefix}{i:03d}" for i in range(n))
