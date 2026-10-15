"""METADATA branch: whitelist selection, tenant-scope override, and schema guard.

No Postgres: the branch tests swap `AsyncSessionLocal` for a fake that records the SQL
and params; the schema test runs every whitelisted query on SQLite against a table built
from `Document.__table__`, so a renamed/removed column fails here instead of at runtime.
"""

import asyncio

import pytest
from sqlalchemy import create_engine, text

from app.agent.state import RoutedAgentState
from app.models.document import Document
from app.routing.branches import metadata
from app.routing.branches.metadata import (
    _WHITELISTED_QUERIES,
    MetadataBranch,
    MetadataQueryRequest,
    MetadataQueryType,
)


class _FixedExtractor:
    def __init__(self, request: MetadataQueryRequest):
        self._request = request

    async def extract(self, query):
        return self._request


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, statement, params):
        self.calls.append((str(statement), params))
        return _Result(self._rows)


def _state() -> RoutedAgentState:
    return RoutedAgentState(
        tenant_id="tenant-a",
        user_id="user-a",
        knowledge_base_id="kb-a",
        query="q",
        resolved_query="q",
    )


def _run(monkeypatch, request, rows=()):
    session = _FakeSession(list(rows))
    monkeypatch.setattr(metadata, "AsyncSessionLocal", lambda: session)
    out = asyncio.run(MetadataBranch(extractor=_FixedExtractor(request))(_state()))
    return session, out


def test_by_author_is_not_whitelisted():
    assert "BY_AUTHOR" not in MetadataQueryType.__members__
    with pytest.raises(ValueError):
        MetadataQueryRequest(query_type="BY_AUTHOR", params={"author": "x"})


def test_trusted_scope_overrides_extractor_params(monkeypatch):
    request = MetadataQueryRequest(
        query_type=MetadataQueryType.BY_VERSION,
        params={"filename": "a.pdf", "knowledge_base_id": "kb-evil", "tenant_id": "t-evil", "user_id": "u-evil"},
    )
    session, _ = _run(monkeypatch, request)

    sql, params = session.calls[0]
    assert sql == _WHITELISTED_QUERIES[MetadataQueryType.BY_VERSION]
    assert params["knowledge_base_id"] == "kb-a"
    assert params["tenant_id"] == "tenant-a"
    assert params["user_id"] == "user-a"


def test_file_extension_is_normalized(monkeypatch):
    request = MetadataQueryRequest(
        query_type=MetadataQueryType.BY_DOCUMENT_TYPE, params={"file_extension": " .PDF "}
    )
    session, _ = _run(monkeypatch, request)

    assert session.calls[0][1]["file_extension"] == "pdf"


def test_rows_are_returned_as_dicts(monkeypatch):
    request = MetadataQueryRequest(query_type=MetadataQueryType.BY_VERSION, params={"filename": "a.pdf"})
    rows = [{"id": "d1", "filename": "a.pdf", "version": 1}]
    _, out = _run(monkeypatch, request, rows)

    assert out["sql_documents"] == rows
    assert "rows=1" in out["trace"][-1]


# --- Schema guard (SQLite) -------------------------------------------------

_SCOPE = {"knowledge_base_id": "kb-a", "tenant_id": "tenant-a", "user_id": "user-a", "limit": 20}

_INSERT = text(
    "INSERT INTO documents (id, knowledge_base_id, user_id, tenant_id, filename, file_extension, "
    "mime_type, file_size_bytes, storage_bucket, storage_key, status, created_dt, deleted_at) "
    "VALUES (:id, :kb, :user_id, :tenant_id, :filename, :ext, 'application/pdf', 1, 'b', 'k', "
    "'INDEXED', :created_dt, :deleted_at)"
)


@pytest.fixture
def conn():
    engine = create_engine("sqlite://")
    Document.__table__.create(engine)
    with engine.connect() as connection:
        rows = [
            ("d1", "kb-a", "user-a", "a.pdf", "pdf", "2026-10-01 00:00:00", None),
            ("d2", "kb-a", "user-a", "a.pdf", "pdf", "2026-10-05 00:00:00", None),
            ("d3", "kb-a", "user-a", "a.pdf", "pdf", "2026-10-06 00:00:00", "2026-10-07 00:00:00"),
            ("d4", "kb-b", "user-a", "a.pdf", "pdf", "2026-10-02 00:00:00", None),
            ("d5", "kb-a", "user-a", "b.md", "md", "2026-10-03 00:00:00", None),
        ]
        for id_, kb, user_id, filename, ext, created_dt, deleted_at in rows:
            connection.execute(
                _INSERT,
                {"id": id_, "kb": kb, "user_id": user_id, "tenant_id": "tenant-a", "filename": filename,
                 "ext": ext, "created_dt": created_dt, "deleted_at": deleted_at},
            )
        yield connection
    engine.dispose()


_EXTRA_PARAMS = {
    MetadataQueryType.BY_DATE_RANGE: {"start_date": "2026-10-01", "end_date": "2026-10-31"},
    MetadataQueryType.BY_DOCUMENT_TYPE: {"file_extension": "pdf"},
    MetadataQueryType.BY_VERSION: {"filename": "a.pdf"},
}


@pytest.mark.parametrize("query_type", list(MetadataQueryType))
def test_every_whitelisted_query_runs_against_documents_schema(conn, query_type):
    conn.execute(text(_WHITELISTED_QUERIES[query_type]), {**_SCOPE, **_EXTRA_PARAMS[query_type]}).all()


def test_by_version_numbers_uploads_of_same_filename(conn):
    rows = conn.execute(
        text(_WHITELISTED_QUERIES[MetadataQueryType.BY_VERSION]), {**_SCOPE, "filename": "a.pdf"}
    ).mappings().all()

    # d3 is soft-deleted, d4 is in another KB: neither counts as a version.
    assert [(r["id"], r["version"]) for r in rows] == [("d2", 2), ("d1", 1)]
