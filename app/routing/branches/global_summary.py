"""GLOBAL 分支：document summary index / map-reduce。

租戶隔離：`knowledge_base_id` 一律用 `common.enforce_knowledge_base_id()` 從
state 強制取出，不信任 router 輸出的 `state.filters`。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.core.logging import get_logger
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

logger = get_logger(__name__)

ROUTER_VERSION = "global-summary-v2"

_NO_SUMMARIES_MESSAGE = "這個知識庫目前沒有可用的摘要索引，請先確認 ingestion pipeline 是否已產生 document summary。"
_PARTIAL_FAILURE_NOTICE = "\n\n（注意：部分文件摘要處理失敗，這個回答沒有涵蓋知識庫中的所有文件。）"
_TRUNCATED_NOTICE = "\n\n（注意：知識庫共有 {total} 份文件，這個回答只納入其中 {used} 份。）"


@dataclass(frozen=True)
class _MapReduceResult:
    answer: str
    failed_batches: int  # map batches whose documents are left out of the answer
    token_count: int  # total_tokens summed over every LLM call that returned

async def fetch_document_summaries(tenant_id:str, user_id:str, knowledge_base_id: str) -> list[Chunk]:
    get_all_docs_stmt = select(Document.id, Document.filename, Document.summary).where(
        Document.knowledge_base_id == uuid.UUID(knowledge_base_id),
        Document.tenant_id == uuid.UUID(tenant_id),
        Document.user_id == uuid.UUID(user_id),
        Document.status == IngestionStatus.INDEXED,
        Document.deleted_at.is_(None),
        Document.summary.is_not(None),
        Document.summary != "",
    ).order_by(
        # Fixed order keeps batch grouping (and so the answer) stable, and decides which
        # documents survive `max_documents` truncation. `id` breaks ties and NULL `created_dt`.
        Document.created_dt,
        Document.id,
    )
    async with get_db_ctx() as db:
        all_docs_result = (await db.execute(get_all_docs_stmt)).all()

    # `document_id` matches the key ingestion writes into Chroma chunk metadata; stored as
    # str because `documents` is checkpointed and must stay serializable.
    return [
        Chunk(
            chunk_id=str(doc.id),
            content=doc.summary,
            source=doc.filename,
            metadata={"document_id": str(doc.id)},
        )
        for doc in all_docs_result
    ]

class GlobalSummaryBranch:
    """`global_summary_node` 的實作（修正原本 `global_summary_node` 是正確的命名。"""

    router_version = ROUTER_VERSION

    def __init__(
        self,
        batch_size: int = 8, # one query with 8 chunks
        guard_size: int = 10, # process-wide cap on concurrent LLM calls, shared by every request
        max_failure_ratio: float = 0.5, # raise when more than this share of a round's map batches fail
        max_documents: int = 200, # caps LLM calls per query; summaries past it are left out
    ):
        if batch_size < 2:
            raise ValueError("batch_size must be at least 2")
        if guard_size < 1:
            raise ValueError("guard_size must be at least 1")
        if not 0 <= max_failure_ratio <= 1:
            raise ValueError("max_failure_ratio must be between 0 and 1")
        if max_documents < 1:
            raise ValueError("max_documents must be at least 1")
        self._batch_size = batch_size
        # Created once so concurrent requests on the module-level `global_summary_node` share
        # it. Safe at import time: asyncio.Semaphore binds to the running loop only on its
        # first contended acquire, so it must not be shared across event loops after that.
        self._guard = asyncio.Semaphore(guard_size)
        self._max_failure_ratio = max_failure_ratio
        self._max_documents = max_documents

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

        total_docs = len(summaries)
        summaries = summaries[: self._max_documents]
        # Label each summary with its filename so the answer can say which document a point is from.
        texts = [f"[{s.source}]\n{s.content}" for s in summaries]
        result = await self._map_reduce(query, texts, knowledge_base_id)
        answer = result.answer
        if len(summaries) < total_docs:
            answer += _TRUNCATED_NOTICE.format(used=len(summaries), total=total_docs)
        if result.failed_batches:
            answer += _PARTIAL_FAILURE_NOTICE
        return {
            "answer": answer,
            "documents": summaries,
            # Absolute, not added to state.token_count: GLOBAL never passes through
            # `retrieve_docs`, which is what resets the counters each run.
            "token_count": result.token_count,
            "trace": state.trace
            + [
                f"global: docs={len(summaries)}/{total_docs} failed_batches={result.failed_batches} "
                f"tokens={result.token_count} kb={knowledge_base_id}"
            ],
        }

    '''
    50 份摘要
      ├─ 第 1 輪：7 個 batch → 7 次 LLM（並行）→ 7 段中間摘要
      └─ 第 2 輪：1 個 batch → 1 次 LLM          → 最終答案

    500 份 →  63 → 8 → 1   （3 輪，共 72 次呼叫，但只等 3 個 round trip）
    '''
    async def _map_reduce(self, query: str, texts: list[str], knowledge_base_id: str) -> _MapReduceResult:
        if not texts:
            return _MapReduceResult(answer="", failed_batches=0, token_count=0)
        token_count = 0
        async def complete(prompt: ChatPromptTemplate, batch: list[str]) -> str:
                nonlocal token_count
                llm = LLMFactory.get_model()
                messages = prompt.format_messages(query=query, summaries="\n\n".join(batch))
                # Every LLM call, the final ANSWER included, counts against the shared cap.
                async with self._guard:
                    response = await llm.ainvoke(messages)
                usage = getattr(response, "usage_metadata", None) or {}
                token_count += usage.get("total_tokens", 0)
                content = response.content
                return content if isinstance(content, str) else str(content)
        current = texts
        failed_batches = 0
        # Map only while the summaries do not fit one call; the last call always answers
        # the question, including when there is a single document.
        while len(current) > self._batch_size:
            batches = [current[i : i + self._batch_size] for i in range(0, len(current), self._batch_size)]
            results = list(
                await asyncio.gather(*(complete(GLOBAL_SUMMARY_MAP_PROMPT, batch) for batch in batches), return_exceptions=True)
            )
            current, failures = [], 0
            for batch_index, r in enumerate(results):
                if isinstance(r, BaseException) and not isinstance(r, Exception):
                    # return_exceptions=True also collects CancelledError / KeyboardInterrupt;
                    # those must propagate, not count as a failed batch.
                    raise r
                if isinstance(r, Exception):
                    failures += 1
                    logger.warning(
                        "global_summary.map_batch_failed",
                        batch_index=batch_index,
                        knowledge_base_id=knowledge_base_id,
                        error_type=type(r).__name__,
                        error=str(r),
                    )
                else:
                    current.append(r)
            if not current:
                raise RuntimeError("all map batches failed")
            if failures / len(batches) > self._max_failure_ratio:
                raise RuntimeError(f"{failures}/{len(batches)} map batches failed")
            failed_batches += failures
        answer = await complete(GLOBAL_SUMMARY_ANSWER_PROMPT, current)
        return _MapReduceResult(answer=answer, failed_batches=failed_batches, token_count=token_count)


global_summary_node = GlobalSummaryBranch()
