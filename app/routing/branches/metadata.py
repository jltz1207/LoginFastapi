"""METADATA 分支：白名單參數化查詢，查 PostgreSQL 的 `document`/`document_chunk`。

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

from app.db.session import AsyncSessionLocal
from app.llm.factory import LLMFactory
from app.routing.branches.common import enforce_knowledge_base_id

if TYPE_CHECKING:  # type-only: keeps `routing` free of a runtime import on `agent`
    from app.agent.state import RoutedAgentState

ROUTER_VERSION = "metadata-v1"

_DEFAULT_LIMIT = 20


class MetadataQueryType(str, Enum):
    BY_AUTHOR = "BY_AUTHOR"
    BY_DATE_RANGE = "BY_DATE_RANGE"
    BY_DOCUMENT_TYPE = "BY_DOCUMENT_TYPE"
    BY_VERSION = "BY_VERSION"


# 白名單查詢：SQL 文字固定，只允許透過具名參數綁定變動內容。
_WHITELISTED_QUERIES: dict[MetadataQueryType, str] = {
    MetadataQueryType.BY_AUTHOR: (
        "SELECT id, title, author, created_at FROM document "
        "WHERE knowledge_base_id = %(knowledge_base_id)s AND author = %(author)s "
        "ORDER BY created_at DESC LIMIT %(limit)s"
    ),
    MetadataQueryType.BY_DATE_RANGE: (
        "SELECT id, title, author, created_at FROM document "
        "WHERE knowledge_base_id = %(knowledge_base_id)s "
        "AND created_at BETWEEN %(start_date)s AND %(end_date)s "
        "ORDER BY created_at DESC LIMIT %(limit)s"
    ),
    MetadataQueryType.BY_DOCUMENT_TYPE: (
        "SELECT id, title, author, document_type, created_at FROM document "
        "WHERE knowledge_base_id = %(knowledge_base_id)s AND document_type = %(document_type)s "
        "ORDER BY created_at DESC LIMIT %(limit)s"
    ),
    MetadataQueryType.BY_VERSION: (
        "SELECT id, title, version, created_at FROM document_chunk "
        "WHERE knowledge_base_id = %(knowledge_base_id)s AND document_id = %(document_id)s "
        "ORDER BY version DESC LIMIT %(limit)s"
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
            "BY_AUTHOR(author) / BY_DATE_RANGE(start_date, end_date) / "
            "BY_DOCUMENT_TYPE(document_type) / BY_VERSION(document_id)。"
            "不要輸出任何 SQL，只輸出查詢類型與參數。"
        )
        messages = [("system", instructions), ("human", query)]
        return await self._get_structured_model().ainvoke(messages)




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
        # knowledge_base_id，這樣即使 extractor 塞了別的 knowledge_base_id 也無效。
        params = {
            **request.params,
            "limit": self._default_limit,
            "knowledge_base_id": knowledge_base_id,
        }
        async with AsyncSessionLocal() as session:
            rows = await session.execute(sql, params)
            rows = rows.fetchall()
            rows = [dict(row) for row in rows]
        answer = _format_rows(request.query_type, rows)

        return {
            "answer": answer,
            "documents": rows,
            "trace": state.trace
            + [f"metadata: type={request.query_type.value} kb={knowledge_base_id} rows={len(rows)}"],
        }


metadata_query_node = MetadataBranch()
