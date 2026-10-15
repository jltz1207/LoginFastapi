"""METADATA branch: whitelist selection, tenant-scope override, and schema guard.

No Postgres: the branch tests swap `AsyncSessionLocal` for a fake that records the SQL
and params; the schema test runs every whitelisted query on SQLite against a table built
from `Document.__table__`, so a renamed/removed column fails here instead of at runtime.
"""

import asyncio
import json
import uuid
from datetime import date, datetime, timezone

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
    _build_query_params,
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
    def __init__(self, rows, error: Exception | None = None):
        self._rows = rows
        self._error = error
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, statement, params):
        self.calls.append((str(statement), params))
        if self._error:
            raise self._error
        return _Result(self._rows)


def _state() -> RoutedAgentState:
    return RoutedAgentState(
        tenant_id="tenant-a",
        user_id="user-a",
        knowledge_base_id="kb-a",
        query="q",
        resolved_query="q",
    )


def _run(monkeypatch, request, rows=(), error=None):
    session = _FakeSession(list(rows), error)
    monkeypatch.setattr(metadata, "AsyncSessionLocal", lambda: session)
    out = asyncio.run(MetadataBranch(extractor=_FixedExtractor(request))(_state()))
    return session, out


def test_by_author_is_not_whitelisted():
    assert "BY_AUTHOR" not in MetadataQueryType.__members__
    with pytest.raises(ValueError):
        MetadataQueryRequest(query_type="BY_AUTHOR")


def test_trusted_scope_cannot_come_from_extractor(monkeypatch):
    # The request model has no scope fields; anything the LLM adds is dropped.
    request = MetadataQueryRequest(
        query_type=MetadataQueryType.BY_VERSION,
        filename="a.pdf",
        knowledge_base_id="kb-evil",
        tenant_id="t-evil",
        user_id="u-evil",
    )
    session, _ = _run(monkeypatch, request)

    sql, params = session.calls[0]
    assert sql == _WHITELISTED_QUERIES[MetadataQueryType.BY_VERSION]
    assert params["knowledge_base_id"] == "kb-a"
    assert params["tenant_id"] == "tenant-a"
    assert params["user_id"] == "user-a"


def test_only_params_of_the_selected_query_type_are_sent(monkeypatch):
    request = MetadataQueryRequest(
        query_type=MetadataQueryType.BY_DOCUMENT_TYPE, file_extension=" .PDF ", filename="a.pdf"
    )
    session, _ = _run(monkeypatch, request)

    params = session.calls[0][1]
    assert params["file_extension"] == "pdf"
    assert "filename" not in params


def test_date_range_end_date_is_inclusive(monkeypatch):
    request = MetadataQueryRequest(
        query_type=MetadataQueryType.BY_DATE_RANGE, start_date="2026-10-01", end_date="2026-10-05"
    )
    session, _ = _run(monkeypatch, request)

    params = session.calls[0][1]
    assert params["start_date"] == date(2026, 10, 1)
    assert params["end_date_exclusive"] == date(2026, 10, 6)


@pytest.mark.parametrize(
    "request_kwargs, expected",
    [
        ({"query_type": MetadataQueryType.BY_VERSION}, "filename"),
        ({"query_type": MetadataQueryType.BY_DATE_RANGE, "start_date": "2026-10-01"}, "end_date"),
        ({"query_type": MetadataQueryType.BY_DOCUMENT_TYPE, "file_extension": "xlsx"}, "xlsx"),
        (
            {"query_type": MetadataQueryType.BY_DATE_RANGE, "start_date": "2026-10-05", "end_date": "2026-10-01"},
            "晚於",
        ),
    ],
)
def test_invalid_request_degrades_without_querying(monkeypatch, request_kwargs, expected):
    session, out = _run(monkeypatch, MetadataQueryRequest(**request_kwargs))

    assert session.calls == []
    assert expected in out["answer"]
    assert out["sql_documents"] == []
    assert "invalid request" in out["trace"][-1]


def test_extractor_failure_degrades(monkeypatch):
    class _BrokenExtractor:
        async def extract(self, query):
            raise RuntimeError("llm down")

    session = _FakeSession([])
    monkeypatch.setattr(metadata, "AsyncSessionLocal", lambda: session)
    out = asyncio.run(MetadataBranch(extractor=_BrokenExtractor())(_state()))

    assert session.calls == []
    assert out["sql_documents"] == []
    assert "extractor failed (RuntimeError: llm down)" in out["trace"][-1]


def test_db_failure_degrades_without_leaking_sql_into_trace(monkeypatch):
    request = MetadataQueryRequest(query_type=MetadataQueryType.BY_VERSION, filename="a.pdf")
    session, out = _run(monkeypatch, request, error=RuntimeError("SELECT ... secret params"))

    assert out["answer"] == "查詢文件資料時發生錯誤，請稍後再試。"
    assert out["sql_documents"] == []
    assert out["trace"][-1].startswith("metadata: query failed type=BY_VERSION (RuntimeError)")
    assert "secret" not in out["trace"][-1]


def test_missing_knowledge_base_id_still_fails_fast(monkeypatch):
    request = MetadataQueryRequest(query_type=MetadataQueryType.BY_VERSION, filename="a.pdf")
    state = _state().model_copy(update={"knowledge_base_id": ""})
    monkeypatch.setattr(metadata, "AsyncSessionLocal", lambda: _FakeSession([]))

    with pytest.raises(ValueError, match="knowledge_base_id missing"):
        asyncio.run(MetadataBranch(extractor=_FixedExtractor(request))(state))


_DOC_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
_CREATED = datetime(2026, 10, 5, 15, 0, tzinfo=timezone.utc)


def test_version_rows_are_formatted_and_jsonable(monkeypatch):
    request = MetadataQueryRequest(query_type=MetadataQueryType.BY_VERSION, filename="a.pdf")
    rows = [{"id": _DOC_ID, "filename": "a.pdf", "file_extension": "pdf", "content_hash": None,
             "file_size_bytes": 2048, "created_dt": _CREATED, "version": 1}]
    _, out = _run(monkeypatch, request, rows)

    assert out["answer"] == "a.pdf 共找到 1 個版本（新到舊）：\n- v1：2026-10-05 15:00 上傳，2.0 KB"
    assert out["sql_documents"][0]["id"] == str(_DOC_ID)
    assert out["sql_documents"][0]["created_dt"] == "2026-10-05T15:00:00+00:00"
    json.dumps(out["sql_documents"])
    assert "rows=1" in out["trace"][-1]


def test_document_rows_are_formatted_with_truncation_note(monkeypatch):
    request = MetadataQueryRequest(query_type=MetadataQueryType.BY_DOCUMENT_TYPE, file_extension="pdf")
    rows = [{"id": _DOC_ID, "filename": f"f{i}.pdf", "file_extension": "pdf", "created_dt": _CREATED} for i in range(2)]
    session = _FakeSession(rows)
    monkeypatch.setattr(metadata, "AsyncSessionLocal", lambda: session)
    out = asyncio.run(MetadataBranch(extractor=_FixedExtractor(request), default_limit=2)(_state()))

    assert out["answer"].splitlines() == [
        "找到 2 份文件（新到舊）：",
        "- f0.pdf（PDF，2026-10-05 15:00 上傳）",
        "- f1.pdf（PDF，2026-10-05 15:00 上傳）",
        "（只列出最新的 2 筆，可縮小條件查詢更多。）",
    ]


def test_no_rows_message(monkeypatch):
    request = MetadataQueryRequest(query_type=MetadataQueryType.BY_VERSION, filename="a.pdf")
    _, out = _run(monkeypatch, request, [])

    assert out["answer"] == "沒有找到符合條件的文件。"
    assert out["sql_documents"] == []


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
            ("d6", "kb-a", "user-a", "c.md", "md", "2026-10-05 15:00:00", None),
        ]
        for id_, kb, user_id, filename, ext, created_dt, deleted_at in rows:
            connection.execute(
                _INSERT,
                {"id": id_, "kb": kb, "user_id": user_id, "tenant_id": "tenant-a", "filename": filename,
                 "ext": ext, "created_dt": created_dt, "deleted_at": deleted_at},
            )
        yield connection
    engine.dispose()


_REQUESTS = {
    MetadataQueryType.BY_DATE_RANGE: MetadataQueryRequest(
        query_type=MetadataQueryType.BY_DATE_RANGE, start_date="2026-10-01", end_date="2026-10-31"
    ),
    MetadataQueryType.BY_DOCUMENT_TYPE: MetadataQueryRequest(
        query_type=MetadataQueryType.BY_DOCUMENT_TYPE, file_extension="pdf"
    ),
    MetadataQueryType.BY_VERSION: MetadataQueryRequest(query_type=MetadataQueryType.BY_VERSION, filename="a.pdf"),
}


def _sqlite_params(request: MetadataQueryRequest) -> dict:
    # sqlite3's default date adapter is deprecated; SQLite stores the dates as ISO text anyway.
    params = {k: v.isoformat() if isinstance(v, date) else v for k, v in _build_query_params(request).items()}
    return {**_SCOPE, **params}


@pytest.mark.parametrize("query_type", list(MetadataQueryType))
def test_every_whitelisted_query_runs_against_documents_schema(conn, query_type):
    conn.execute(text(_WHITELISTED_QUERIES[query_type]), _sqlite_params(_REQUESTS[query_type])).all()


def test_date_range_includes_whole_end_day(conn):
    request = MetadataQueryRequest(
        query_type=MetadataQueryType.BY_DATE_RANGE, start_date="2026-10-05", end_date="2026-10-05"
    )
    rows = conn.execute(
        text(_WHITELISTED_QUERIES[MetadataQueryType.BY_DATE_RANGE]), _sqlite_params(request)
    ).mappings().all()

    # d6 was uploaded at 15:00 on the end date; BETWEEN '...' AND '2026-10-05' used to drop it.
    assert [r["id"] for r in rows] == ["d6", "d2"]


def test_by_version_numbers_uploads_of_same_filename(conn):
    rows = conn.execute(
        text(_WHITELISTED_QUERIES[MetadataQueryType.BY_VERSION]),
        _sqlite_params(_REQUESTS[MetadataQueryType.BY_VERSION]),
    ).mappings().all()

    # d3 is soft-deleted, d4 is in another KB: neither counts as a version.
    assert [(r["id"], r["version"]) for r in rows] == [("d2", 2), ("d1", 1)]
