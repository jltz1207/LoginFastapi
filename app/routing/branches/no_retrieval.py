"""NO_RETRIEVAL 分支：常識性問題，零檢索，LLM 直接作答（不是拒答）。

不呼叫 `enforce_knowledge_base_id()`：這個分支定義上完全不碰知識庫/資料庫，
沒有租戶邊界可以跨越，所以不需要（也不應該）強制注入 `knowledge_base_id`。

回答不得附引用來源——`documents` 永遠是空list，且不呼叫任何
`common.format_citations()` 之類的引用格式化。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from app.llm.factory import LLMFactory
from app.rag.prompts import NO_RETRIEVAL_PROMPT

if TYPE_CHECKING:  # type-only: keeps `routing` free of a runtime import on `agent`
    from app.agent.state import RoutedAgentState

ROUTER_VERSION = "no-retrieval-v2"


@dataclass
class DirectAnswer:
    answer: str
    token_count: int = 0


class DirectAnswerLLM(Protocol):
    async def complete(self, query: str) -> DirectAnswer: ...


class LLMDirectAnswerLLM:
    def __init__(self, model=None):
        self._model = model

    async def complete(self, query: str) -> DirectAnswer:
        model = self._model or LLMFactory.get_model()
        response = await model.ainvoke(NO_RETRIEVAL_PROMPT.format_messages(question=query))
        content = response.content
        usage = getattr(response, "usage_metadata", None) or {}
        return DirectAnswer(
            answer=content if isinstance(content, str) else str(content),
            token_count=usage.get("total_tokens", 0),
        )


class NoRetrievalBranch:
    router_version = ROUTER_VERSION

    def __init__(self, llm: DirectAnswerLLM | None = None):
        self._llm = llm or LLMDirectAnswerLLM()

    async def __call__(self, state: RoutedAgentState) -> dict:
        query = state.resolved_query
        result = await self._llm.complete(query)
        # 本次 run 的絕對值（budget counter 是 last-write-wins，見 `app/agent/state.py`）
        return {
            "answer": result.answer,
            "documents": [],
            "token_count": result.token_count,
            "trace": state.trace + ["no_retrieval: zero-retrieval direct answer"],
        }


no_retrieval_node = NoRetrievalBranch()
