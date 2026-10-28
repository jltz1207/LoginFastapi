"""MULTI_HOP 分支：唯一的 agentic 分支。decompose -> 迭代檢索 -> synthesize。

四項護欄缺一不可上線。前三項數值是建構子參數（有預設值，可以調整，但沒有關掉的選項）：
1. `recursion_limit`：最多幾輪 decompose -> retrieve 迭代。
2. `max_cost_units`：累積用量上限 = 檢索字數 + decompose 的 LLM token（近似值，不是校準過的
   業務成本，只用來當安全煞車，不可拿去當 M8/M9 的 cost-weighted error 依據）。
   synthesize 在護欄之後才呼叫，不受這個上限約束，但會算進回寫的 `token_count`。
3. sub-query 去重：同一輪或跨輪重複的 sub-query（正規化後）不再重新檢索。
4. `global_timeout_seconds`：整個 hop 迴圈的全域逾時，用 `asyncio.wait_for` 包住。

租戶隔離：`knowledge_base_id` 一律用 `common.enforce_knowledge_base_id()` 從
state 強制取出，每一跳檢索都帶著它，不信任 router 輸出的 `state.filters`。

`agentic_subgraph` 是 module-level singleton，所有 request 共用：任何 per-request 狀態
（retriever、hops、用量）都不得寫回 `self`。
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Protocol

from langchain_core.runnables import Runnable

from app.llm.factory import LLMFactory
from app.llm.usage import TokenCounter, count_llm_tokens
from pydantic import BaseModel

from app.rag.prompts import MULTI_HOP_DECOMPOSE_PROMPT
from app.rag.reranker.reranker import with_rerank
from app.rag.retriever.hybrid_retriever import HybridRetriever
from app.routing.branches.common import enforce_knowledge_base_id
from app.agent.state import Chunk

if TYPE_CHECKING:  # annotation only; `Chunk` above is already a runtime import from `agent`
    from app.agent.state import RoutedAgentState

ROUTER_VERSION = "multi-hop-v2"


class HopResult(BaseModel):
    sub_query: str
    chunks: list[Chunk]


class QueryDecomposer(Protocol):
    async def decompose(self, query: str, hops_so_far: list[HopResult]) -> list[str]:
        """回傳下一輪要問的 sub-query。空清單代表『資訊已經夠了，可以收斂』。"""
        ...

class Synthesizer(Protocol):
    async def synthesize(self, query: str, hops: list[HopResult]) -> str: ...

def _build_retriever(tenant_id: str, user_id: str, knowledge_base_id: str) -> Runnable:
    """和 LOOKUP（`app/agent/nodes/retrieval.py`）同一套檢索：hybrid（向量 0.6 / BM25 0.4）+ rerank。

    per-request 建立的成本很低：BM25 index 由 `BM25IndexProvider` 快取。
    """
    retriever = HybridRetriever((0.6, 0.4)).get_retriever(tenant_id, user_id, knowledge_base_id, top_k=4)
    return with_rerank(retriever, rerank_top_k=4)


def _normalize(sub_query: str) -> str:
    return re.sub(r"\s+", "", sub_query.strip().lower())

def _estimate_cost(sub_query: str, chunks: list[Chunk]) -> int:
    """檢索部分的 token-ish 用量估算，只給護欄用的近似值，不是校準過的業務成本。"""
    return len(sub_query) + sum(len(c.content) for c in chunks)

# decomposer 每個 hop 看到的摘錄：前幾個 chunk、每個截斷到固定長度，控制 prompt 大小。
_EXCERPT_CHUNKS_PER_HOP = 2
_EXCERPT_CHARS = 300


class _DecomposeOutput(BaseModel):
    sub_queries: list[str]


def _format_hops(hops: list[HopResult]) -> str:
    if not hops:
        return "（尚無）"
    blocks = []
    for hop in hops:
        excerpts = [c.content[:_EXCERPT_CHARS] for c in hop.chunks[:_EXCERPT_CHUNKS_PER_HOP]]
        body = "\n".join(f"  > {e}" for e in excerpts) or "  > （沒有檢索到內容）"
        blocks.append(f"- {hop.sub_query}\n{body}")
    return "\n".join(blocks)


class LLMQueryDecomposer:
    def __init__(self, structured_model=None):
        self._structured_model = structured_model

    def _get_structured_model(self):
        if self._structured_model is None:
            self._structured_model = LLMFactory.get_model().with_structured_output(_DecomposeOutput)
        return self._structured_model

    async def decompose(self, query: str, hops_so_far: list[HopResult]) -> list[str]:
        messages = MULTI_HOP_DECOMPOSE_PROMPT.format_messages(query=query, hops=_format_hops(hops_so_far))
        output = await self._get_structured_model().ainvoke(messages)
        return output.sub_queries

class LLMMultiHopSynthesizer:
    def __init__(self, model=None):
        self._model = model

    async def synthesize(self, query: str, hops: list[HopResult]) -> str:
        blocks = []
        for hop in hops:
            content = "\n".join(c.content for c in hop.chunks)
            blocks.append(f"Sub-query: {hop.sub_query}\n{content}")
        context = "\n\n".join(blocks)
        messages = [
            ("system", "根據以下多輪檢索結果，統整回答原始問題。"),
            ("human", f"{context}\n\n原始問題：{query}"),
        ]
        llm = self._model or LLMFactory.get_model()
        response = await llm.ainvoke(messages)
        content = response.content
        return content if isinstance(content, str) else str(content)


class MultiHopBranch:
    """`agentic_subgraph` 的實作。"""

    router_version = ROUTER_VERSION

    def __init__(
        self,
        decomposer: QueryDecomposer | None = None,
        retriever: Runnable | None = None,
        synthesizer: Synthesizer | None = None,
        recursion_limit: int = 4,
        max_cost_units: int = 20_000,
        global_timeout_seconds: float = 30.0,
    ):
        self._decomposer = decomposer or LLMQueryDecomposer()
        self._retriever = retriever
        self._synthesizer = synthesizer or LLMMultiHopSynthesizer()
        self._recursion_limit = recursion_limit
        self._max_cost_units = max_cost_units
        self._global_timeout_seconds = global_timeout_seconds

    async def retrieve(self, retriever: Runnable, sub_query: str) -> list[Chunk]:
        docs = await retriever.ainvoke(sub_query)
        # 保留 chunk metadata（含 `document_id`），下游 sources / citation 才找得到出處。
        return [Chunk(chunk_id=doc.id, content=doc.page_content, metadata=doc.metadata) for doc in docs]

    async def __call__(self, state: RoutedAgentState) -> dict:
        knowledge_base_id = enforce_knowledge_base_id(state)
        # Injected retriever wins (tests); otherwise build one scoped to this request's
        # tenant. Never cache it on self: `agentic_subgraph` is a module-level singleton,
        # so writing back would leak the first request's tenant scope to every later user.
        retriever = self._retriever or _build_retriever(state.tenant_id, state.user_id, knowledge_base_id)
        query = state.resolved_query

        # 由這裡建立、`_run_hops` 就地 append：逾時取消 `_run_hops` 後，已完成的 hop 仍然保留，
        # 和其他三個護欄一樣是「停下來但保留成果」。
        hops: list[HopResult] = []
        guardrail_hits: list[str] = []
        # 收集這個 branch 所有 LLM 呼叫的 usage（decompose 是 structured output，拿不到 `usage_metadata`）
        with count_llm_tokens() as usage:
            try:
                await asyncio.wait_for(
                    self._run_hops(retriever, query, hops, guardrail_hits, usage),
                    timeout=self._global_timeout_seconds,
                )
            except asyncio.TimeoutError:
                guardrail_hits.append("global_timeout")

            if hops:
                answer = await self._synthesizer.synthesize(query, hops)
            else:
                answer = "多跳查詢在護欄限制內沒有取得足夠資訊，無法完整回答。"
            token_count = usage.total_tokens

        return {
            "answer": answer,
            "documents": [chunk for hop in hops for chunk in hop.chunks],
            # 本次 run 的絕對值（budget counter 是 last-write-wins，見 `app/agent/state.py`）
            "token_count": token_count,
            "trace": state.trace
            + [f"multi_hop: hops={len(hops)} kb={knowledge_base_id} guardrails={guardrail_hits}"],
        }

    async def _run_hops(
        self,
        retriever: Runnable,
        query: str,
        hops: list[HopResult],
        guardrail_hits: list[str],
        usage: TokenCounter,
    ) -> None:
        asked: set[str] = set()
        retrieval_units = 0

        for _ in range(self._recursion_limit):
            sub_queries = await self._decomposer.decompose(query, hops)
            # 過濾與登記同一步做：同一輪內正規化後相同的 sub-query 也只檢索一次
            new_sub_queries = []
            for q in sub_queries:
                key = _normalize(q)
                if key not in asked:
                    asked.add(key)
                    new_sub_queries.append(q)
            if not new_sub_queries:
                break

            # 同一輪的 sub-query 彼此沒有依賴，並行檢索；結果依原順序 append。
            # cost 檢查因此在整輪結束後做，一輪最多超出上限一輪的量。
            results = await asyncio.gather(*(self.retrieve(retriever, q) for q in new_sub_queries))
            for sub_query, chunks in zip(new_sub_queries, results):
                retrieval_units += _estimate_cost(sub_query, chunks)
                hops.append(HopResult(sub_query=sub_query, chunks=chunks))
            # cost = 檢索字數 + 目前為止 decompose 用掉的 LLM token
            if retrieval_units + usage.total_tokens >= self._max_cost_units:
                guardrail_hits.append("cost_cap")
                return
        else:
            guardrail_hits.append("recursion_limit")


agentic_subgraph = MultiHopBranch()
