"""POST /upload only queues the document; GET /documents/{id} is scoped to the caller.

Endpoint functions are called directly with asyncio.run and a fake AsyncSession, as in
tests/auth (no pytest-asyncio, no DB).
"""

import asyncio
import io
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError
from starlette.datastructures import Headers

from app.api.v1.endpoints import knowledgeBases as kb_endpoint
from app.models.document import Document, IngestionStatus


class FakeSession:
    def __init__(self, result=None, commit_error=None):
        self.added, self.statements = [], []
        self._result, self._commit_error = result, commit_error

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        if self._commit_error:
            raise self._commit_error

    async def refresh(self, obj):
        obj.id = obj.id or uuid.uuid4()

    async def execute(self, stmt):
        self.statements.append(stmt)
        result = self._result
        return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: result))


@pytest.fixture
def user():
    return SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), username="alice")


@pytest.fixture
def env(monkeypatch, user):
    kb = SimpleNamespace(id=uuid.uuid4(), user_id=user.id, tenant_id=user.tenant_id)
    calls = SimpleNamespace(uploaded=[], deleted=[], notified=0)

    async def owned_kb(db, knowledge_base_id, current_user):
        return kb

    def upload_file(tenant_id, user_id, knowledge_base_id, file_bytes, filename, mime_type):
        calls.uploaded.append((tenant_id, user_id, knowledge_base_id, filename))
        return {"storage_bucket": "root", "storage_key": f"tenants/{tenant_id}/{filename}"}

    def notify():
        calls.notified += 1

    def must_not_run(*args, **kwargs):
        raise AssertionError("upload must not process the document inline")

    monkeypatch.setattr(kb_endpoint, "get_owned_knowledge_base", owned_kb)
    monkeypatch.setattr(kb_endpoint, "upload_file", upload_file)
    monkeypatch.setattr(kb_endpoint, "delete_file", lambda bucket, key: calls.deleted.append(key))
    monkeypatch.setattr(kb_endpoint, "notify_ingestion_worker", notify)
    monkeypatch.setattr("app.ingestion.processor.process_document", must_not_run)
    monkeypatch.setattr("app.vectorstore.StoreIndexer.StoreIndexer.add_documents", must_not_run)
    return SimpleNamespace(kb=kb, calls=calls)


def _file(name="notes.md", data=b"# hello"):
    return UploadFile(file=io.BytesIO(data), filename=name, headers=Headers({"content-type": "text/markdown"}))


def _upload(db, user, file, kb_id="ignored"):
    return asyncio.run(kb_endpoint.uploadDocument(file=file, knowledge_base_id=kb_id, db=db, current_user=user))


def test_upload_returns_pending_immediately(env, user):
    db = FakeSession()

    response = _upload(db, user, _file())

    assert response.document.status == IngestionStatus.PENDING
    assert response.document.page_count is None and response.document.has_summary is False
    (doc,) = db.added
    assert doc.status == IngestionStatus.PENDING
    assert (doc.tenant_id, doc.user_id, doc.knowledge_base_id) == (user.tenant_id, user.id, env.kb.id)
    assert doc.raw_text is None and doc.chunk_count is None
    assert doc.created_by == "alice"
    assert env.calls.uploaded == [(user.tenant_id, user.id, env.kb.id, "notes.md")]
    assert env.calls.notified == 1
    assert env.calls.deleted == []


def test_upload_route_answers_202():
    route = next(r for r in kb_endpoint.router.routes if getattr(r, "path", "") == "/upload")

    assert route.status_code == 202


@pytest.mark.parametrize("name", ["report.docx", "noext", "image.png"])
def test_unsupported_extension_is_400_not_500(env, user, name):
    with pytest.raises(HTTPException) as exc:
        _upload(FakeSession(), user, _file(name=name))

    assert exc.value.status_code == 400
    assert env.calls.uploaded == []


def test_too_large_is_400(env, user):
    with pytest.raises(HTTPException) as exc:
        _upload(FakeSession(), user, _file(data=b"x" * (10 * 1024 * 1024 + 1)))

    assert exc.value.status_code == 400


def test_failed_commit_removes_the_stored_file(env, user):
    db = FakeSession(commit_error=OperationalError("INSERT", {}, Exception("db down")))

    with pytest.raises(HTTPException) as exc:
        _upload(db, user, _file())

    assert exc.value.status_code == 500
    assert env.calls.deleted == [f"tenants/{user.tenant_id}/notes.md"]
    assert env.calls.notified == 0


def _document(user, **overrides):
    values = dict(
        id=uuid.uuid4(), knowledge_base_id=uuid.uuid4(), tenant_id=user.tenant_id, user_id=user.id,
        filename="a.md", file_extension="md", file_size_bytes=1, mime_type="text/markdown",
        status=IngestionStatus.FAILED, error_message="ValueError: corrupt", summary=None,
    )
    values.update(overrides)
    return Document(**values)


def test_status_endpoint_returns_progress(user):
    doc = _document(user)

    response = asyncio.run(kb_endpoint.getDocument(doc.id, db=FakeSession(result=doc), current_user=user))

    assert response.status == IngestionStatus.FAILED
    assert response.error_message == "ValueError: corrupt"


def test_status_endpoint_is_scoped_and_hides_foreign_ids(user):
    db = FakeSession(result=None)
    doc_id = uuid.uuid4()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(kb_endpoint.getDocument(doc_id, db=db, current_user=user))

    assert exc.value.status_code == 404
    sql = str(db.statements[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert f"documents.id = '{doc_id}'" in sql
    assert f"documents.tenant_id = '{user.tenant_id}'" in sql
    assert f"documents.user_id = '{user.id}'" in sql
    assert "documents.deleted_at IS NULL" in sql
