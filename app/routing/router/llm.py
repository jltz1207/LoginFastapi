"""Gemini structured-output router（cascade 第三層，最慢/最準，~500ms）。

Confidence 刻意不讓 LLM 自報：structured output schema（`_LLMRouteOutput`）只有
`route`/`reasoning`，不含 confidence 欄位。Confidence 另外用 `EmbeddingRouter`
對 LLM 選出的 route 算相似度分數（複用 M2 embedding 層的範例向量），符合
「confidence 一律用 embedding 相似度或 logprobs，不用 LLM 自報信心」的規則。

Prompt 組裝把固定的 taxonomy instructions/few-shot（`_FIXED_PREFIX`，模組載入時
只組一次）與逐次呼叫變動的 query 分開。傳入 `prompt_cache`（M6，見
`routing/cache/prompt_cache.py`）就會真的接上 Gemini 端的 explicit context
caching：成功的話只送變動的 query，不重複貼一次固定前綴；快取本身失敗（例如內容
沒到 Gemini 最小快取門檻）就靜默退回原本「固定前綴 + 變動內容」都送的路徑——
這是效能最佳化，不能讓它變成新的故障點。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from app.routing.cache.prompt_cache import PromptCache
from app.routing.router.base import BaseRouter
from app.routing.router.embedding import ROUTE_EXAMPLES, EmbeddingRouter
from app.routing.taxonomy import Route, RouteDecision, RouterContext

ROUTER_VERSION = "llm-v1"

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
        prompt_cache: PromptCache | None = None,
        model_factory=None,
    ):
        """`structured_model` 需提供 async `ainvoke(messages) -> _LLMRouteOutput`
        介面（真正型別是 `.with_structured_output(_LLMRouteOutput)` 包過的
        LangChain chat model）。留空則用 `model_factory` 建立；測試請注入假物件。

        `prompt_cache`：可選。設定後每次呼叫會先嘗試把 `_FIXED_PREFIX` 快取到
        Gemini 端，成功就只送變動的 query；失敗就靜默退回不快取的路徑。

        `model_factory`：`(cached_content: str | None) -> 原始 chat model` 的
        建構函式，預設用 Gemini flash（`cached_content` 非 None 時才會傳給它）。
        """
        self._structured_model = structured_model
        self._confidence_scorer = confidence_scorer or EmbeddingRouter()
        self._prompt_cache = prompt_cache
        self._model_factory = model_factory or self._default_model_factory

    @staticmethod
    def _default_model_factory(cached_content: str | None):
        from langchain_google_genai import ChatGoogleGenerativeAI

        kwargs = {"model": "gemini-2.5-flash", "temperature": 0}
        if cached_content:
            kwargs["cached_content"] = cached_content
        return ChatGoogleGenerativeAI(**kwargs)

    async def _resolve_cached_content(self) -> str | None:
        if self._prompt_cache is None:
            return None
        try:
            return await self._prompt_cache.get_cache_handle(self.router_version, _FIXED_PREFIX)
        except Exception:  # noqa: BLE001 - 快取失敗要靜默退回不快取，不能讓最佳化變成故障點
            return None

    async def route(self, query: str, ctx: RouterContext) -> RouteDecision:
        if self._structured_model is not None:
            structured_model = self._structured_model
            messages = [("system", _FIXED_PREFIX), ("human", query)]
        else:
            cached_content = await self._resolve_cached_content()
            base_model = self._model_factory(cached_content)
            structured_model = base_model.with_structured_output(_LLMRouteOutput)
            if cached_content:
                # 固定前綴已經在 Gemini 端快取，這裡只送變動的 query，不重複貼一次
                messages = [("human", query)]
            else:
                messages = [("system", _FIXED_PREFIX), ("human", query)]

        output = await structured_model.ainvoke(messages)
        confidence = await self._confidence_scorer.score_route(query, output.route)
        return RouteDecision(
            route=output.route,
            confidence=confidence,
            reasoning=output.reasoning,
        )
