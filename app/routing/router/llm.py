"""LLM structured-output router（cascade 第三層，最慢/最準，~500ms）。

Confidence 刻意不讓 LLM 自報：structured output schema（`_LLMRouteOutput`）只有
`route`/`reasoning`，不含 confidence 欄位。Confidence 另外用 `EmbeddingRouter`
對 LLM 選出的 route 算相似度分數（複用 M2 embedding 層的範例向量），符合
「confidence 一律用 embedding 相似度或 logprobs，不用 LLM 自報信心」的規則。

這個 confidence **只供觀測**，不決定是否採用：LLM 成功回傳就 `accepted=True`。
會走到這一層，代表 embedding 相似度已經低於門檻，而這裡的 confidence 是同一張
相似度表中的一格，必定也低於門檻；拿它比門檻會讓 LLM 的決定永遠被丟棄。
也因為只供觀測，算 confidence 失敗（embedding API 故障）時記 log 並給 0.0，
LLM 的決定照常回傳，不讓 embedding API 的故障連帶讓 LLM 層失敗。

模型由 `LLMFactory.get_model()` 提供（主模型依 `LLM_PROVIDER_TYPE`，失敗時走設定的 fallback
model），與各分支一致，不再寫死 Gemini。原本 Gemini 專屬的 explicit context caching
（`prompt_cache`）從未接上、且 provider 不再固定，已移除。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from app.core.logging import get_logger
from app.llm.factory import LLMFactory
from app.routing.router.base import BaseRouter
from app.routing.router.embedding import ROUTE_EXAMPLES, EmbeddingRouter
from app.routing.taxonomy import Route, RouteDecision, RouterContext

ROUTER_VERSION = "llm-v3"

logger = get_logger(__name__)

_SYSTEM_INSTRUCTIONS = """你是一個 RAG 系統的 query router。
你的任務是把使用者的問題分類到以下 6 個類別之一：

- LOOKUP：針對知識庫內容的點狀查詢，一次檢索即可回答。
- GLOBAL：需要跨整個文件集做摘要/歸納才能回答的問題。
- METADATA：查詢文件的結構化屬性（作者、日期、版本、檔案類型…）。
- MULTI_HOP：需要多次、彼此依賴的迭代檢索才能回答（比較、因果鏈）。
- NO_RETRIEVAL：常識性問題，不需要檢索知識庫，直接回答即可。
- META：關於這個系統本身的問題（"你能做什麼"、"你是誰"）。

只能輸出上述 6 個類別其中一個，不可以輸出其他值。"""


def _few_shot_block() -> str:
    lines = []
    for route, examples in ROUTE_EXAMPLES.items():
        for example in examples:
            lines.append(f"- {route.value}: {example}")
    return "\n".join(lines)


_FIXED_PREFIX = _SYSTEM_INSTRUCTIONS + "\n\n分類範例：\n" + _few_shot_block()


class _LLMRouteOutput(BaseModel):
    route: Route
    reasoning: Optional[str] = None


class LLMRouter(BaseRouter):
    router_version = ROUTER_VERSION

    def __init__(
        self,
        structured_model=None,
        confidence_scorer: EmbeddingRouter | None = None,
    ):
        """`structured_model` 需提供 async `ainvoke(messages) -> _LLMRouteOutput`
        介面（真正型別是 `.with_structured_output(_LLMRouteOutput)` 包過的
        LangChain chat model）。留空則第一次呼叫時用 `LLMFactory.get_model()` 建立並重用
        （instance 由 `router/defaults.py` 跨請求共用）；測試請注入假物件。
        """
        self._structured_model = structured_model
        self._confidence_scorer = confidence_scorer or EmbeddingRouter()

    def _get_structured_model(self):
        if self._structured_model is None:
            # `get_model()` 回傳 RunnableWithFallbacks，`with_structured_output` 會一併套到每個 fallback
            self._structured_model = LLMFactory.get_model().with_structured_output(_LLMRouteOutput)
        return self._structured_model

    async def route(self, query: str, ctx: RouterContext) -> RouteDecision:
        structured_model = self._get_structured_model()
        messages = [("system", _FIXED_PREFIX), ("human", query)]

        output = await structured_model.ainvoke(messages)
        try:
            confidence = await self._confidence_scorer.score_route(query, output.route)
        except Exception:  # noqa: BLE001 - confidence 只供觀測，embedding API 故障不能拖垮 LLM 層
            logger.warning("llm router confidence scoring failed", route=output.route.value, exc_info=True)
            confidence = 0.0
        return RouteDecision(
            route=output.route,
            confidence=confidence,
            accepted=True,
            reasoning=output.reasoning,
        )
