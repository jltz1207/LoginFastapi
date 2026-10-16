"""Process 共用的預設 router instance。

graph 每次請求都重新 build（`get_compiled_graph()` 是 per-request dependency），
如果 `RouterNode` 每次都 new 新的 router，`EmbeddingRouter` 存在 instance 上的範例向量
快取就只活一個請求，每次請求都要重新 embed 36 句例句。這裡用 `lru_cache` 建立
process 層級的 singleton（同 `get_chroma_db()` 的做法），並讓 cascade、fallback chain
與 `LLMRouter` 的 confidence scorer 共用同一個 `EmbeddingRouter`。

共用的前提是 router 不在 instance 上存逐請求狀態：降級紀錄隨
`RouteDecision.degradations` 回傳；`EmbeddingRouter` 的兩個快取（範例向量、query
vector）內容只由文字決定，跨請求、跨租戶共用都不會讀到別人的結果。
建構這些物件都不會呼叫外部 API（Gemini client 都是第一次用到時才建立）。
"""

from __future__ import annotations

from functools import lru_cache

from app.routing.router.cascade import CascadeRouter
from app.routing.router.embedding import EmbeddingRouter
from app.routing.router.fallback_chain import FallbackChainRouter
from app.routing.router.llm import LLMRouter
from app.routing.router.rule import RuleRouter


@lru_cache(maxsize=1)
def get_shared_embedding_router() -> EmbeddingRouter:
    return EmbeddingRouter()


@lru_cache(maxsize=1)
def get_shared_llm_router() -> LLMRouter:
    return LLMRouter(confidence_scorer=get_shared_embedding_router())


@lru_cache(maxsize=1)
def get_default_router() -> CascadeRouter:
    return CascadeRouter(
        rule=RuleRouter(),
        embedding=get_shared_embedding_router(),
        llm=get_shared_llm_router(),
    )


@lru_cache(maxsize=1)
def get_default_fallback() -> FallbackChainRouter:
    return FallbackChainRouter(
        llm=get_shared_llm_router(),
        embedding=get_shared_embedding_router(),
        rule=RuleRouter(),
    )
