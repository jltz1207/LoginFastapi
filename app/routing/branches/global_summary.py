"""GLOBAL 分支：document summary index / map-reduce。

租戶隔離：`knowledge_base_id` 一律用 `common.enforce_knowledge_base_id()` 從
state 強制取出，不信任 router 輸出的 `state.filters`。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from app.db.session import get_db_ctx
from app.llm.factory import LLMFactory
from app.models.document import Document, IngestionStatus
from app.rag.prompts import GLOBAL_SUMMARY_ANSWER_PROMPT, GLOBAL_SUMMARY_MAP_PROMPT
from app.routing.branches.common import Chunk, enforce_knowledge_base_id

from langchain_core.prompts import ChatPromptTemplate
from sqlalchemy import select
if TYPE_CHECKING:  # type-only: keeps `routing` free of a runtime import on `agent`
    from app.agent.state import RoutedAgentState
import uuid

ROUTER_VERSION = "global-summary-v2"

_NO_SUMMARIES_MESSAGE = "這個知識庫目前沒有可用的摘要索引，請先確認 ingestion pipeline 是否已產生 document summary。"

async def fetch_document_summaries(tenant_id:str, user_id:str, knowledge_base_id: str) -> list[Chunk]:
    get_all_docs_stmt = select(Document.id, Document.summary).where(
        Document.knowledge_base_id == uuid.UUID(knowledge_base_id),
        Document.tenant_id == uuid.UUID(tenant_id),
        Document.user_id == uuid.UUID(user_id),
        Document.status == IngestionStatus.INDEXED,
        Document.deleted_at.is_(None),
        Document.summary.is_not(None)
    )
    async with get_db_ctx() as db:
        all_docs_result = (await db.execute(get_all_docs_stmt)).all()

    return [Chunk(chunk_id=str(doc.id), content=doc.summary or "", metadata={"id": doc.id}) for doc in all_docs_result]

class GlobalSummaryBranch:
    """`global_summary_node` 的實作（修正原本 `global_summary_node` 是正確的命名。"""

    router_version = ROUTER_VERSION

    def __init__(
        self,
        batch_size: int = 8, # one query with 8 chunks
        guard_size: int = 10 # Max: 10 process at the same time
    ):
        if batch_size < 2:
            raise ValueError("batch_size must be at least 2")
        self._batch_size = batch_size
        self._guard_size = guard_size

    async def __call__(self, state: RoutedAgentState) -> dict:
        knowledge_base_id = enforce_knowledge_base_id(state)
        query = state.resolved_query
        summaries = await fetch_document_summaries(state.tenant_id, state.user_id, knowledge_base_id)
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

    '''
    50 份摘要
      ├─ 第 1 輪：7 個 batch → 7 次 LLM（並行）→ 7 段中間摘要
      └─ 第 2 輪：1 個 batch → 1 次 LLM          → 最終答案

    500 份 →  63 → 8 → 1   （3 輪，共 72 次呼叫，但只等 3 個 round trip）
    '''
    async def _map_reduce(self, query: str, texts: list[str]) -> str:
        if not texts:
            return ""
        sem = asyncio.Semaphore(self._guard_size)
        async def complete(prompt: ChatPromptTemplate, batch: list[str]) -> str:
                llm = LLMFactory.get_model()
                messages = prompt.format_messages(query=query, summaries="\n\n".join(batch))
                response = await llm.ainvoke(messages)
                content = response.content
                return content if isinstance(content, str) else str(content)
        async def _guarded(batch: list[str]) -> str:
            async with sem:
                return await complete(GLOBAL_SUMMARY_MAP_PROMPT, batch)
        current = texts
        # Map only while the summaries do not fit one call; the last call always answers
        # the question, including when there is a single document.
        while len(current) > self._batch_size:
            batches = [current[i : i + self._batch_size] for i in range(0, len(current), self._batch_size)]
            results = list(
                await asyncio.gather(*(_guarded(batch) for batch in batches), return_exceptions=True)
            )
            current = [r for r in results if isinstance(r, str)]
            if not current:
                raise RuntimeError("all map batches failed")
        return await complete(GLOBAL_SUMMARY_ANSWER_PROMPT, current)


global_summary_node = GlobalSummaryBranch()
