"""NO_RETRIEVAL 分支：常識性問題，零檢索，LLM 直接作答（不是拒答）。

不呼叫 `enforce_knowledge_base_id()`：這個分支定義上完全不碰知識庫/資料庫，
沒有租戶邊界可以跨越，所以不需要（也不應該）強制注入 `knowledge_base_id`。

回答不得附引用來源——`documents` 永遠是空list，且不呼叫任何
`common.format_citations()` 之類的引用格式化。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from app.llm.factory import LLMFactory

if TYPE_CHECKING:  # type-only: keeps `routing` free of a runtime import on `agent`
    from app.agent.state import RoutedAgentState

ROUTER_VERSION = "no-retrieval-v1"


class DirectAnswerLLM(Protocol):
    async def complete(self, query: str) -> str: ...


class LLMDirectAnswerLLM:
    def __init__(self, model=None):
        self._model = model

    async def complete(self, query: str) -> str:
        if self._model is not None:
            model = self._model
            response = await model.ainvoke(query)
            content = response.content
            return content if isinstance(content, str) else str(content)
        factory = LLMFactory()
        model = factory.get_model()
        response = await model.ainvoke(query)
        content = response.content
        return content if isinstance(content, str) else str(content)


class NoRetrievalBranch:
    router_version = ROUTER_VERSION

    def __init__(self, llm: DirectAnswerLLM | None = None):
        self._llm = llm or LLMDirectAnswerLLM()

    async def __call__(self, state: RoutedAgentState) -> dict:
        query = state.resolved_query
        answer = await self._llm.complete(query)
        return {
            "answer": answer,
            "documents": [],
            "trace": state.trace + ["no_retrieval: zero-retrieval direct answer"],
        }


no_retrieval_node = NoRetrievalBranch()
