"""process_document: status transitions, idempotent re-runs and tenant scope."""

import asyncio
import uuid

from langchain_core.documents import Document as LCDocument

from app.ingestion.processor import ERROR_MESSAGE_MAX_CHARS, build_raw_text, chunk_id, process_document
from app.models.document import IngestionStatus
from app.vectorstore.StoreIndexer import StoreIndexer

from ingestion_fakes import (
    FakeRepository,
    FakeStorage,
    FakeSummarizer,
    SharedCollectionManager,
    all_records,
    make_deps,
    make_indexer,
    make_record,
    words,
)


def _run(document_id, deps):
    async def go():
        claimed = await deps.repository.claim_next()
        assert claimed is not None and claimed[0] == document_id
        return await process_document(*claimed, deps)

    return asyncio.run(go())


def _setup(text: str, **record_kwargs):
    record = make_record(**record_kwargs)
    repo = FakeRepository(record)
    storage = FakeStorage(files={record.storage_key: text})
    return record, repo, storage


def test_success_path_indexes_chunks_and_summary():
    record, repo, storage = _setup(words(200))  # ~1600 chars → several chunks
    summarizer = FakeSummarizer()
    deps = make_deps(repo, storage=storage, summarizer=summarizer)

    assert repo.rows[record.id].status == IngestionStatus.PENDING
    result = _run(record.id, deps)

    row = repo.rows[record.id]
    assert result == IngestionStatus.INDEXED
    assert row.status == IngestionStatus.INDEXED
    assert row.indexed and row.error_message is None
    assert row.record.summary == f"summary of {len(row.raw_text)} chars"
    assert len(summarizer.calls) == 1
    records = all_records(deps.store_indexer)
    assert row.chunk_count == len(records) > 1
    assert [r[0] for r in records] == sorted(chunk_id(str(record.id), i) for i in range(len(records)))
    for _, metadata in records:
        assert metadata["document_id"] == str(record.id)
        assert metadata["tenant_id"] == str(record.tenant_id)
        assert metadata["user_id"] == str(record.user_id)
        assert metadata["knowledge_base_id"] == str(record.knowledge_base_id)


def test_raw_text_is_loader_output_not_joined_chunks():
    text = words(200)
    record, repo, storage = _setup(text)

    _run(record.id, make_deps(repo, storage=storage))

    raw_text = repo.rows[record.id].raw_text
    # Chunk overlap (100 chars) would repeat words; the old "\n." join added stray dots.
    assert raw_text == text
    assert "\n." not in raw_text


def test_loader_failure_marks_failed_without_touching_chroma():
    record, repo, storage = _setup("anything")
    storage.load = lambda ext, data: (_ for _ in ()).throw(ValueError("corrupt pdf"))
    deps = make_deps(repo, storage=storage)

    result = _run(record.id, deps)

    row = repo.rows[record.id]
    assert result == IngestionStatus.FAILED
    assert row.status == IngestionStatus.FAILED
    assert row.error_message == "ValueError: corrupt pdf"
    assert not row.indexed
    assert all_records(deps.store_indexer) == []


def test_unsupported_extension_fails_with_readable_message():
    record, repo, storage = _setup("text")
    repo.rows[record.id].record = record.__class__(**{**record.__dict__, "file_extension": "docx"})

    _run(record.id, make_deps(repo, storage=storage))

    assert repo.rows[record.id].error_message == "unsupported file type: docx"


class _FailsAfterWrite(StoreIndexer):
    """Writes the chunks, then raises: a partial failure that leaves chunks behind."""

    def add_documents(self, *args, **kwargs):
        super().add_documents(*args, **kwargs)
        raise RuntimeError("embedding quota exceeded")


def test_failure_after_chroma_write_removes_the_chunks():
    record, repo, storage = _setup(words(200))
    indexer = _FailsAfterWrite(collection_manager=SharedCollectionManager())

    result = _run(record.id, make_deps(repo, indexer=indexer, storage=storage))

    assert result == IngestionStatus.FAILED
    assert repo.rows[record.id].error_message == "RuntimeError: embedding quota exceeded"
    assert all_records(indexer) == []  # a FAILED document must not stay searchable


def test_error_message_is_truncated():
    record, repo, storage = _setup("text")
    storage.load = lambda ext, data: (_ for _ in ()).throw(ValueError("x" * 10_000))

    _run(record.id, make_deps(repo, storage=storage))

    assert len(repo.rows[record.id].error_message) == ERROR_MESSAGE_MAX_CHARS


def test_summary_failure_still_indexes_document():
    record, repo, storage = _setup(words(200))
    deps = make_deps(repo, storage=storage, summarizer=FakeSummarizer(error=TimeoutError("llm down")))

    result = _run(record.id, deps)

    row = repo.rows[record.id]
    assert result == IngestionStatus.INDEXED
    assert row.record.summary is None  # GLOBAL skips it
    assert row.error_message == "summary failed: TimeoutError: llm down"
    assert len(all_records(deps.store_indexer)) == row.chunk_count > 0


def test_retry_does_not_duplicate_chunks_or_summary():
    record, repo, storage = _setup(words(200))
    summarizer = FakeSummarizer()
    deps = make_deps(repo, storage=storage, summarizer=summarizer)

    _run(record.id, deps)
    first = all_records(deps.store_indexer)
    repo.rows[record.id].status = IngestionStatus.PENDING  # e.g. lease expired, requeued
    _run(record.id, deps)

    assert all_records(deps.store_indexer) == first
    assert len(summarizer.calls) == 1  # summary already stored: not regenerated
    assert repo.rows[record.id].attempt_count == 2


def test_retry_with_fewer_chunks_removes_the_stale_ones():
    record, repo, storage = _setup(words(400))
    deps = make_deps(repo, storage=storage)
    _run(record.id, deps)
    before = len(all_records(deps.store_indexer))

    # Upserting deterministic ids alone would leave chunks [new_count, before) behind.
    storage.files[record.storage_key] = words(100)
    repo.rows[record.id].status = IngestionStatus.PENDING
    _run(record.id, deps)

    after = all_records(deps.store_indexer)
    assert len(after) == repo.rows[record.id].chunk_count < before


def test_scope_comes_from_the_row_not_from_loader_metadata():
    record, repo, storage = _setup(words(100))
    # A loader key with the same name as a scope key must not win.
    storage.loader_metadata = {"tenant_id": "evil", "knowledge_base_id": "evil", "document_id": "evil"}
    deps = make_deps(repo, storage=storage)

    _run(record.id, deps)

    for _, metadata in all_records(deps.store_indexer):
        assert metadata["tenant_id"] == str(record.tenant_id)
        assert metadata["knowledge_base_id"] == str(record.knowledge_base_id)
        assert metadata["document_id"] == str(record.id)


def test_rerun_in_tenant_a_does_not_touch_tenant_b():
    shared_doc_id = uuid.uuid4()
    a = make_record(tenant="tA", user="uA", kb="kb", storage_key="a", doc_id=shared_doc_id)
    indexer = make_indexer()
    # Tenant B holds chunks under the same kb name *and* the same document_id
    # (worst case: a forged or colliding id). Only the full scope tells them apart.
    b = make_record(tenant="tB", user="uB", kb="kb", storage_key="b")
    b_meta = {
        "tenant_id": str(b.tenant_id),
        "user_id": str(b.user_id),
        "knowledge_base_id": str(a.knowledge_base_id),
        "document_id": str(shared_doc_id),
    }
    indexer.add_documents(
        [LCDocument(page_content="tenant B secret")],
        tenant_id=b_meta["tenant_id"],
        user_id=b_meta["user_id"],
        metadata=b_meta,
        ids=["b-chunk"],
    )
    repo = FakeRepository(a)
    deps = make_deps(repo, indexer=indexer, storage=FakeStorage(files={"a": words(200)}))

    _run(a.id, deps)
    repo.rows[a.id].status = IngestionStatus.PENDING
    _run(a.id, deps)  # re-run deletes A's chunks first

    records = dict(all_records(indexer))
    assert records["b-chunk"] == b_meta
    assert all(m["tenant_id"] == str(a.tenant_id) for cid, m in records.items() if cid != "b-chunk")


def test_lost_lease_writes_nothing_more():
    record, repo, storage = _setup(words(100))
    deps = make_deps(repo, storage=storage)

    async def go():
        claimed = await repo.claim_next()
        # Another worker re-claimed the row after our lease expired.
        repo.rows[record.id].attempt_count += 1
        return await process_document(*claimed, deps)

    assert asyncio.run(go()) is None
    row = repo.rows[record.id]
    assert row.status == IngestionStatus.PROCESSING and row.raw_text is None
    assert all_records(deps.store_indexer) == []


def test_missing_row_is_skipped():
    repo = FakeRepository()

    assert asyncio.run(process_document(uuid.uuid4(), 1, make_deps(repo))) is None


def test_build_raw_text_does_not_mutate_pages():
    pages = [LCDocument(page_content="line one\nline two.\n\n\n\nnext")]

    build_raw_text(pages)

    assert pages[0].page_content == "line one\nline two.\n\n\n\nnext"
