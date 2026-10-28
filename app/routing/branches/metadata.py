"""METADATA 分支：白名單參數化查詢，查 PostgreSQL 的 `documents`。

**禁止 text-to-SQL**：SQL 文字全部是模組層級的固定常數（`_WHITELISTED_QUERIES`），
從使用者輸入到最終執行之間唯一允許變動的是「選哪一個白名單查詢」+「綁定參數」，
SQL 字串本身永遠不會被拼接或改寫。負責從自然語言萃取「查哪一種、參數是什麼」的
`MetadataQueryExtractor` 只能輸出 `MetadataQueryRequest`（`MetadataQueryType` enum +
固定型別的參數欄位），不能輸出任意 SQL 片段，也不能輸出任意參數 key。

租戶隔離：`knowledge_base_id` 一律用 `common.enforce_knowledge_base_id()` 從
state 強制取出；`MetadataQueryRequest` 本身沒有 `knowledge_base_id`/`tenant_id`/
`user_id` 欄位，SQL 參數裡的這三個值只來自 state。

降級：extractor 失敗、參數不完整、DB 查詢失敗都不會讓 graph 掛掉，而是回一段說明
並把原因記進 `trace`。唯一例外是缺 `knowledge_base_id`——那是租戶邊界，照樣 fail fast。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.llm.factory import LLMFactory
from app.rag.loaders.base_loader import base_loader
from app.routing.branches.common import enforce_knowledge_base_id

if TYPE_CHECKING:  # type-only: keeps `routing` free of a runtime import on `agent`
    from app.agent.state import RoutedAgentState

logger = get_logger(__name__)

ROUTER_VERSION = "metadata-v3"

_DEFAULT_LIMIT = 20

# 直接引用上傳 endpoint 檢查的同一個 tuple（app/api/v1/endpoints/knowledgeBases.py），兩邊不會再分歧。
_ALLOWED_FILE_EXTENSIONS = base_loader.SUPPORTED_EXTENSIONS


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
    # 半開區間：end_date 當天整天都要算進去，所以比較的是「隔天 00:00 之前」。
    MetadataQueryType.BY_DATE_RANGE: (
        "SELECT id, filename, file_extension, created_dt FROM documents "
        + _SCOPE
        + "AND created_dt >= :start_date AND created_dt < :end_date_exclusive "
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

# 每種查詢必填的 MetadataQueryRequest 欄位。
_REQUIRED_FIELDS: dict[MetadataQueryType, tuple[str, ...]] = {
    MetadataQueryType.BY_DATE_RANGE: ("start_date", "end_date"),
    MetadataQueryType.BY_DOCUMENT_TYPE: ("file_extension",),
    MetadataQueryType.BY_VERSION: ("filename",),
}


class MetadataQueryRequest(BaseModel):
    """extractor 的輸出。參數是固定型別的欄位而不是 `dict`：
    LLM structured output 對無型別 dict 支援差，且固定欄位讓 extractor 無法塞入任意 key。"""

    query_type: MetadataQueryType
    start_date: date | None = Field(default=None, description="BY_DATE_RANGE: inclusive start date, YYYY-MM-DD")
    end_date: date | None = Field(default=None, description="BY_DATE_RANGE: inclusive end date, YYYY-MM-DD")
    file_extension: str | None = Field(
        default=None, description=f"BY_DOCUMENT_TYPE: one of {', '.join(_ALLOWED_FILE_EXTENSIONS)}"
    )
    filename: str | None = Field(default=None, description="BY_VERSION: exact file name, e.g. report.pdf")

    @field_validator("file_extension")
    @classmethod
    def _normalize_file_extension(cls, value: str | None) -> str | None:
        # 上傳時 file_extension 存小寫、不含點。
        return value.strip().lstrip(".").lower() if value is not None else None

    @field_validator("filename")
    @classmethod
    def _strip_filename(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class InvalidMetadataQuery(ValueError):
    """extractor 的輸出不足以組出合法查詢（缺必填參數、值不合法）。訊息會直接回給使用者。"""


class MetadataQueryExtractor(Protocol):
    async def extract(self, query: str) -> MetadataQueryRequest: ...


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
            f"今天是 {date.today().isoformat()}。"
            "把使用者問題轉成以下白名單查詢類型之一，並只填入該類型需要的欄位："
            "BY_DATE_RANGE(start_date, end_date；YYYY-MM-DD，兩端都包含) / "
            f"BY_DOCUMENT_TYPE(file_extension: {' | '.join(_ALLOWED_FILE_EXTENSIONS)}) / BY_VERSION(filename)。"
            "不要輸出任何 SQL，只輸出查詢類型與參數。"
        )
        messages = [("system", instructions), ("human", query)]
        return await self._get_structured_model().ainvoke(messages)


def _build_query_params(request: MetadataQueryRequest) -> dict:
    """依查詢類型挑出並驗證參數；只回傳該類型 SQL 用得到的 key。"""
    query_type = request.query_type
    missing = [name for name in _REQUIRED_FIELDS[query_type] if not getattr(request, name)]
    if missing:
        raise InvalidMetadataQuery(f"{query_type.value} 查詢缺少必要條件：{', '.join(missing)}。")

    if query_type is MetadataQueryType.BY_DATE_RANGE:
        if request.start_date > request.end_date:
            raise InvalidMetadataQuery(
                f"日期範圍不合法：開始日期 {request.start_date} 晚於結束日期 {request.end_date}。"
            )
        return {"start_date": request.start_date, "end_date_exclusive": request.end_date + timedelta(days=1)}

    if query_type is MetadataQueryType.BY_DOCUMENT_TYPE:
        if request.file_extension not in _ALLOWED_FILE_EXTENSIONS:
            raise InvalidMetadataQuery(
                f"不支援的文件類型：{request.file_extension}（只支援 {', '.join(_ALLOWED_FILE_EXTENSIONS)}）。"
            )
        return {"file_extension": request.file_extension}

    return {"filename": request.filename}


def _format_datetime(value: object) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if isinstance(value, datetime) else str(value or "-")


def _format_size(num_bytes: object) -> str:
    if not isinstance(num_bytes, int):
        return "-"
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / (1024 * 1024):.1f} MB"


def _format_rows(query_type: MetadataQueryType, rows: list[dict], limit: int) -> str:
    if not rows:
        return "沒有找到符合條件的文件。"

    if query_type is MetadataQueryType.BY_VERSION:
        header = f"{rows[0]['filename']} 共找到 {len(rows)} 個版本（新到舊）："
        lines = [
            f"- v{row['version']}：{_format_datetime(row['created_dt'])} 上傳，{_format_size(row['file_size_bytes'])}"
            for row in rows
        ]
    else:
        header = f"找到 {len(rows)} 份文件（新到舊）："
        lines = [
            f"- {row['filename']}（{str(row['file_extension']).upper()}，{_format_datetime(row['created_dt'])} 上傳）"
            for row in rows
        ]

    if len(rows) >= limit:
        lines.append(f"（只列出最新的 {limit} 筆，可縮小條件查詢更多。）")
    return "\n".join([header, *lines])


def _to_jsonable(row: dict) -> dict:
    """`sql_documents` 會被 checkpointer 序列化：UUID/datetime 先轉成字串。"""
    return {
        key: str(value) if isinstance(value, UUID) else value.isoformat() if isinstance(value, (date, datetime)) else value
        for key, value in row.items()
    }


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
        # 租戶邊界：缺 knowledge_base_id 必須 fail fast，所以放在所有 try 之外。
        knowledge_base_id = enforce_knowledge_base_id(state)
        query = state.resolved_query

        def _degraded(answer: str, reason: str) -> dict:
            return {
                "answer": answer,
                "sql_documents": [],
                "trace": state.trace + [f"metadata: {reason} kb={knowledge_base_id}"],
            }

        try:
            request = await self._extractor.extract(query)
        except Exception as exc:  # noqa: BLE001 - extractor 失敗要降級，不能讓整個 graph 掛掉
            return _degraded(
                "無法從問題判斷要查哪一種文件資料，請換個說法，例如指定日期範圍、文件類型或檔名。",
                f"extractor failed ({type(exc).__name__}: {exc})",
            )

        try:
            query_params = _build_query_params(request)
        except InvalidMetadataQuery as exc:
            return _degraded(str(exc), f"invalid request type={request.query_type.value} ({exc})")

        sql = _WHITELISTED_QUERIES[request.query_type]
        # 租戶範圍的三個值只來自 state；放在最後，確保不會被任何查詢參數覆蓋。
        params = {
            **query_params,
            "limit": self._default_limit,
            "knowledge_base_id": knowledge_base_id,
            "tenant_id": state.tenant_id,
            "user_id": state.user_id,
        }
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(text(sql), params)
                rows = [dict(row) for row in result.mappings().all()]
        except Exception as exc:  # noqa: BLE001 - DB 失敗要降級，不能讓整個 graph 掛掉
            # DB 錯誤訊息含 SQL 與參數，只寫進 log；trace 只留例外型別。
            logger.exception("metadata query failed", query_type=request.query_type.value)
            return _degraded(
                "查詢文件資料時發生錯誤，請稍後再試。",
                f"query failed type={request.query_type.value} ({type(exc).__name__})",
            )

        answer = _format_rows(request.query_type, rows, self._default_limit)
        return {
            "answer": answer,
            "sql_documents": [_to_jsonable(row) for row in rows],
            "trace": state.trace
            + [f"metadata: type={request.query_type.value} kb={knowledge_base_id} rows={len(rows)}"],
        }


metadata_query_node = MetadataBranch()
