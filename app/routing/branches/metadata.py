"""METADATA 分支：白名單參數化查詢，查 PostgreSQL 的 `documents`。

**禁止 text-to-SQL**：SQL 文字全部是模組層級的固定常數（`_WHITELISTED_QUERIES`），
從使用者輸入到最終執行之間唯一允許變動的是「選哪一個白名單查詢」+「綁定參數」，
SQL 字串本身永遠不會被拼接或改寫。負責從自然語言萃取「查哪一種、參數是什麼」的
`MetadataQueryExtractor` 只能輸出 `MetadataQueryType` enum + 參數 dict，不能輸出
任意 SQL 片段。

租戶隔離：`knowledge_base_id` 一律用 `common.enforce_knowledge_base_id()` 從
state 強制取出；即使 extractor 因為被誘導（prompt injection 等）而在參數裡塞了
別的 `knowledge_base_id`，也會在組 params 時被強制覆蓋掉，絕不送進 SQL 執行。
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, Field
from sqlalchemy import text

from app.db.session import AsyncSessionLocal
from app.llm.factory import LLMFactory
from app.routing.branches.common import enforce_knowledge_base_id

if TYPE_CHECKING:  # type-only: keeps `routing` free of a runtime import on `agent`
    from app.agent.state import RoutedAgentState

ROUTER_VERSION = "metadata-v1"

_DEFAULT_LIMIT = 20


class MetadataQueryType(str, Enum):
    BY_DATE_RANGE = "BY_DATE_RANGE"
    BY_DOCUMENT_TYPE = "BY_DOCUMENT_TYPE"
    BY_VERSION = "BY_VERSION"


# 共用的 WHERE：租戶/使用者/知識庫隔離 + 排除 soft delete。
_SCOPE = (
    "WHERE knowledge_base_id = :knowledge_base_id AND tenant_id = :tenant_id "
    "AND user_id = :user_id AND deleted_at IS NULL "
)

# 白名單查詢：SQL 文字固定，只允許透過具名參數綁定變動內容。
_WHITELISTED_QUERIES: dict[MetadataQueryType, str] = {
    MetadataQueryType.BY_DATE_RANGE: (
        "SELECT id, filename, file_extension, created_dt FROM documents "
        + _SCOPE
        + "AND created_dt BETWEEN :start_date AND :end_date "
        "ORDER BY created_dt DESC LIMIT :limit"
    ),
    MetadataQueryType.BY_DOCUMENT_TYPE: (
        "SELECT id, filename, file_extension, created_dt FROM documents "
        + _SCOPE
        + "AND file_extension = :file_extension "
        "ORDER BY created_dt DESC LIMIT :limit"
    ),
    # documents 沒有 version 欄位：同一 KB 內同一 filename 的每次上傳視為一個版本，
    # 版本號依 created_dt 由舊到新編號。
    MetadataQueryType.BY_VERSION: (
        "SELECT id, filename, file_extension, content_hash, file_size_bytes, created_dt, "
        "ROW_NUMBER() OVER (ORDER BY created_dt ASC) AS version FROM documents "
        + _SCOPE
        + "AND filename = :filename "
        "ORDER BY created_dt DESC LIMIT :limit"
    ),
}


class MetadataQueryRequest(BaseModel):
    query_type: MetadataQueryType
    params: dict = Field(default_factory=dict)


class MetadataQueryExtractor(Protocol):
    async def extract(self, query: str) -> MetadataQueryRequest: ...


class QueryExecutor(Protocol):
    async def execute(self, sql: str, params: dict) -> list[dict]: ...


class LLMMetadataQueryExtractor:
    """把自然語言問題轉成白名單查詢類型 + 參數（不是 SQL）。"""

    def __init__(self, structured_model=None):
        self._structured_model = structured_model

    def _get_structured_model(self):
        if self._structured_model is None:
            base_model = LLMFactory().get_model()
            self._structured_model = base_model.with_structured_output(MetadataQueryRequest)
        return self._structured_model

    async def extract(self, query: str) -> MetadataQueryRequest:
        instructions = (
            "把使用者問題轉成以下白名單查詢類型之一，並填入對應參數："
            "BY_DATE_RANGE(start_date, end_date) / "
            "BY_DOCUMENT_TYPE(file_extension: pdf | md | docx) / BY_VERSION(filename)。"
            "不要輸出任何 SQL，只輸出查詢類型與參數。"
        )
        messages = [("system", instructions), ("human", query)]
        return await self._get_structured_model().ainvoke(messages)


def _normalize_params(query_type: MetadataQueryType, params: dict) -> dict:
    """把 extractor 的參數轉成和 DB 存法一致的格式（上傳時 file_extension 存小寫、不含點）。"""
    if query_type is MetadataQueryType.BY_DOCUMENT_TYPE and isinstance(params.get("file_extension"), str):
        params = {**params, "file_extension": params["file_extension"].strip().lstrip(".").lower()}
    return params


def _format_rows(query_type: MetadataQueryType, rows: list[dict]) -> str:
    if not rows:
        return f"沒有找到符合 {query_type.value} 條件的文件。"
    lines = [f"- {row}" for row in rows]
    return "\n".join(lines)


class MetadataBranch:
    router_version = ROUTER_VERSION

    def __init__(
        self,
        extractor: MetadataQueryExtractor | None = None,
        default_limit: int = _DEFAULT_LIMIT,
    ):
        self._extractor = extractor or LLMMetadataQueryExtractor()
        self._default_limit = default_limit

    async def __call__(self, state: RoutedAgentState) -> dict:
        knowledge_base_id = enforce_knowledge_base_id(state)
        query = state.resolved_query

        request = await self._extractor.extract(query)
        sql = _WHITELISTED_QUERIES[request.query_type]

        # 順序很重要：先展開 extractor 產出的參數，最後才強制蓋上信任的
        # knowledge_base_id/tenant_id/user_id，這樣即使 extractor 塞了別的值也無效。
        params = {
            **_normalize_params(request.query_type, request.params),
            "limit": self._default_limit,
            "knowledge_base_id": knowledge_base_id,
            "tenant_id": state.tenant_id,
            "user_id": state.user_id,
        }
        async with AsyncSessionLocal() as session:
            result = await session.execute(text(sql), params)
            rows = [dict(row) for row in result.mappings().all()]
        answer = _format_rows(request.query_type, rows)

        return {
            "answer": answer,
            "sql_documents": rows,
            "trace": state.trace
            + [f"metadata: type={request.query_type.value} kb={knowledge_base_id} rows={len(rows)}"],
        }


metadata_query_node = MetadataBranch()
