"""Query routing taxonomy。

6 類定案（2026-08-10，`OUT_OF_SCOPE` 已刪除，不得復用）。新增或合併類別前，
必須先對 >=20 條樣本做雙人獨立標註，一致率 < 90% 禁止 merge（見
`eval/annotator_agreement.py`）。
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Route(str, Enum):
    """RAG query 的分派路徑，共 6 類。

    邊界判準（尤其容易混淆的幾組）：

    - **NO_RETRIEVAL vs LOOKUP**：問題是否需要「這個知識庫裡的具體事實」。
      "牛頓第一定律是什麼" -> NO_RETRIEVAL（常識，LLM 直接答，零檢索）；
      "我們知識庫裡這份文件怎麼描述牛頓定律" -> LOOKUP（需要具體文件內容）。
    - **NO_RETRIEVAL vs META**：NO_RETRIEVAL 問的是「世界知識」，META 問的是「這個系統本身」。
      "你能做什麼/你是誰/這個系統有哪些功能" -> META；"什麼是機器學習" -> NO_RETRIEVAL。
    - **LOOKUP vs GLOBAL**：LOOKUP 是點狀查詢（找特定段落/事實）；GLOBAL 需要跨整個
      文件集做摘要/歸納才能回答（"這份文件在講什麼"、"所有文件的共同主題是什麼"）。
    - **LOOKUP vs MULTI_HOP**：MULTI_HOP 需要多次、彼此依賴的迭代檢索才能回答（比較、
      因果鏈、需要先查 A 再用 A 的結果查 B）；LOOKUP 一次檢索即可回答。
    - **METADATA**：查詢文件的結構化屬性（作者、日期、文件類型、版本…），走白名單參數化
      查詢 PostgreSQL，不查向量庫內容本身，禁止 text-to-SQL。

    `FOLLOW_UP` 不是這裡的一類：它在進入 router 之前就被 Query Resolver
    (`routing/resolver.py`) 消歧成完整問句，router 因此永遠只看到獨立、完整的問句。
    """

    LOOKUP = "LOOKUP"
    """預設路徑。針對知識庫內容的點狀查詢：hybrid retrieval -> rerank -> 生成 + 引用。"""

    GLOBAL = "GLOBAL"
    """需要跨文件集摘要/歸納才能回答的問題。走 document summary index / map-reduce。"""

    METADATA = "METADATA"
    """查詢文件的結構化屬性（作者、日期、類型…）。白名單參數化查詢 PostgreSQL，禁止 text-to-SQL。"""

    MULTI_HOP = "MULTI_HOP"
    """需要多次、彼此依賴的迭代檢索才能回答的問題。唯一的 agentic 分支：
    decompose -> 迭代檢索 -> synthesize。"""

    NO_RETRIEVAL = "NO_RETRIEVAL"
    """常識性問題，不需要檢索知識庫，LLM 直接作答（零檢索，非拒答）。回答不附引用來源。"""

    META = "META"
    """關於系統本身的問題（例如「你能做什麼」）。固定模板回覆或回傳 trace，不即時生成。"""


DEFAULT_ROUTE: Route = Route.LOOKUP
"""任何分支都不能把 default 改成拒答；誤判時拒答的使用者體感最差。"""

CONFIDENCE_THRESHOLD: float = 0.70
"""Embedding router 與 cascade 短路判斷統一用這個門檻。
不可與 semantic cache 的相似度門檻共用同一常數（該門檻須用真實資料另行校準）。"""


class RouteDecision(BaseModel):
    """Router 每一層（rule/embedding/LLM）都必須回傳的輸出 schema。"""

    route: Route
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="必須來自 embedding 相似度或 logprobs，不可為 LLM 自報信心",
    )
    filters: dict = Field(
        default_factory=dict,
        description=(
            "僅供除錯/trace 參考。租戶隔離一律由 leaf node 強制注入 "
            "knowledge_base_id，禁止任何下游邏輯信任這裡的值做租戶邊界判斷。"
        ),
    )
    reasoning: Optional[str] = Field(
        default=None,
        description="可選，router 分類理由，供除錯與觀測使用，不作為信心來源。",
    )


class RouterContext(BaseModel):
    """呼叫 router 時的上下文。

    `tenant_id` / `knowledge_base_id` 僅供 trace 記錄使用；分類邏輯只看
    query 語意，不得依賴這裡的租戶資訊。`resolved_history` 是 Query Resolver
    消歧後的對話歷史（純文字），禁止塞入檢索到的文件內容——避免文件內容夾帶
    prompt injection 影響分類結果。
    """

    tenant_id: str
    knowledge_base_id: str
    resolved_history: list[str] = Field(default_factory=list)
