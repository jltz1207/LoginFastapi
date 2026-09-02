"""GLOBAL 分支：document summary index / map-reduce。

讀的是 Celery ingestion worker 產生好的「每份文件摘要」索引（`SummaryIndexClient`），
不是即時對原始文件做檢索——GLOBAL 問題需要跨整個文件集歸納，逐篇即時摘要在延遲/
成本上都不划算。

租戶隔離：`knowledge_base_id` 一律用 `common.enforce_knowledge_base_id()` 從
state 強制取出，不信任 router 輸出的 `state.filters`。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol

from app.db.session import get_db
from app.llm.factory import LLMFactory
from app.models.document import Document, IngestionStatus
from app.routing.branches.common import Chunk, enforce_knowledge_base_id

from sqlalchemy import select
if TYPE_CHECKING:  # type-only: keeps `routing` free of a runtime import on `agent`
    from app.agent.state import RoutedAgentState

ROUTER_VERSION = "global-summary-v1"

_NO_SUMMARIES_MESSAGE = "這個知識庫目前沒有可用的摘要索引，請先確認 ingestion pipeline 是否已產生 document summary。"


class SummaryIndexClient(Protocol):
    async def fetch_document_summaries(self, knowledge_base_id: str) -> list[Chunk]: ...


class MapReduceLLM(Protocol):
    async def complete(self, prompt: str) -> str: ...








    async def complete(self, prompt: str) -> str:
        response = await self._get_model().ainvoke(prompt)
        content = response.content
        return content if isinstance(content, str) else str(content)


def _build_map_prompt(query: str, texts: list[str]) -> str:
    joined = "\n\n".join(texts)
    return (
        f"以下是幾份文件摘要：\n{joined}\n\n"
        f"請針對問題「{query}」，從這些摘要中萃取相關重點，寫成一段精簡摘要。"
    )


class GlobalSummaryBranch:
    """`global_summary_node` 的實作（修正原本 `global_summary_node` 是正確的命名。"""

    router_version = ROUTER_VERSION

    def __init__(
        self,
        index_client: SummaryIndexClient | None = None,
        llm: MapReduceLLM | None = None,
        batch_size: int = 8,
    ):
        self._llm = llm or LLMFactory.get_model()
        self._batch_size = batch_size

    async def fetch_document_summaries(tenant_id:str, user_id:str, knowledge_base_id: str) -> list[Chunk]:
        get_all_docs_stmt = select(Document).where(
            Document.knowledge_base_id == knowledge_base_id,
            Document.tenant_id == tenant_id,
            Document.user_id == user_id,
            Document.status == IngestionStatus.INDEXED,
        )
        async with get_db() as db:
            all_docs_result = await db.execute(get_all_docs_stmt).scalars().all()
            
        return [Chunk(content=doc.summary or "", metadata={"id": doc.id}) for doc in documents]
        
    
    async def __call__(self, state: RoutedAgentState) -> dict:
        knowledge_base_id = enforce_knowledge_base_id(state)
        query = state.resolved_query

        summaries = await self.fetch_document_summaries(knowledge_base_id)
        if not summaries:
            return {
                "answer": _NO_SUMMARIES_MESSAGE,
                "documents": [],
                "trace": state.trace + [f"global: no summaries kb={knowledge_base_id}"],
            }

        answer = await self._map_reduce(query, [s.content for s in summaries])
        return {
            "answer": answer,
            "documents": summaries,
            "trace": state.trace + [f"global: docs={len(summaries)} kb={knowledge_base_id}"],
        }

    async def _map_reduce(self, query: str, texts: list[str]) -> str:
        current = texts
        first_pass = True
        while len(current) > 1 or first_pass:
            batches = [current[i : i + self._batch_size] for i in range(0, len(current), self._batch_size)]
            current = list(
                await asyncio.gather(*(self._llm.complete(_build_map_prompt(query, batch)) for batch in batches))
            )
            first_pass = False
        return current[0]


global_summary_node = GlobalSummaryBranch()
