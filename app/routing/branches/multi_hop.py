"""MULTI_HOP 分支：唯一的 agentic 分支。decompose -> 迭代檢索 -> synthesize。

四項護欄缺一不可上線（CLAUDE.md 開發約定），全部是建構子的必填數值型參數，
不能傳 `None` 關掉：
1. `recursion_limit`：最多幾輪 decompose -> retrieve 迭代。
2. `max_cost_units`：累積 token-ish 用量上限（近似值，不是校準過的業務成本，
   只用來當安全煞車，不可拿去當 M8/M9 的 cost-weighted error 依據）。
3. sub-query 去重：同一輪或跨輪重複的 sub-query（正規化後）不再重新檢索。
4. `global_timeout_seconds`：整個 hop 迴圈的全域逾時，用 `asyncio.wait_for` 包住。

租戶隔離：`knowledge_base_id` 一律用 `common.enforce_knowledge_base_id()` 從
state 強制取出，每一跳檢索都帶著它，不信任 router 輸出的 `state.filters`。
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Protocol

from app.llm.factory import LLMFactory
from pydantic import BaseModel

from app.routing.branches.common import Chunk, enforce_knowledge_base_id

if TYPE_CHECKING:  # type-only: keeps `routing` free of a runtime import on `agent`
    from app.agent.state import RoutedAgentState

ROUTER_VERSION = "multi-hop-v1"


class HopResult(BaseModel):
    sub_query: str
    chunks: list[Chunk]


class QueryDecomposer(Protocol):
    async def decompose(self, query: str, hops_so_far: list[HopResult]) -> list[str]:
        """回傳下一輪要問的 sub-query。空清單代表『資訊已經夠了，可以收斂』。"""
        ...


class HopRetriever(Protocol):
    async def retrieve(self, sub_query: str, knowledge_base_id: str) -> list[Chunk]: ...


class Synthesizer(Protocol):
    async def synthesize(self, query: str, hops: list[HopResult]) -> str: ...


def _normalize(sub_query: str) -> str:
    return re.sub(r"\s+", "", sub_query.strip().lower())


def _estimate_cost(sub_query: str, chunks: list[Chunk]) -> int:
    """token-ish 用量估算，只給護欄用的近似值，不是校準過的業務成本。"""
    return len(sub_query) + sum(len(c.content) for c in chunks)


class LLMQueryDecomposer:
    def __init__(self, structured_model=None):
        self._structured_model = structured_model

    def _get_structured_model(self):
        if self._structured_model is not None:
            return self._structured_model
        class _DecomposeOutput(BaseModel):
            sub_queries: list[str]
        base_model = LLMFactory().get_model()
        self._structured_model = base_model.with_structured_output(_DecomposeOutput)
        return self._structured_model

    async def decompose(self, query: str, hops_so_far: list[HopResult]) -> list[str]:
        history = "\n".join(f"- {h.sub_query}" for h in hops_so_far) or "（尚無）"
        instructions = (
            "你在幫一個需要多次檢索才能回答的問題做分解。"
            "根據原始問題與已經查過的 sub-query，列出下一輪還需要查的 sub-query。"
            "如果已經有足夠資訊可以回答原始問題，回傳空清單。"
        )
        messages = [
            ("system", instructions),
            ("human", f"原始問題：{query}\n\n已查過：\n{history}"),
        ]
        output = await self._get_structured_model().ainvoke(messages)
        return output.sub_queries


class ChromaHopRetriever:
    def __init__(self, collection_factory=None):
        self._collection_factory = collection_factory

    def _get_collection(self, knowledge_base_id: str):
        if self._collection_factory is not None:
            return self._collection_factory(knowledge_base_id)
        import chromadb

        client = chromadb.Client()
        return client.get_or_create_collection(f"kb_{knowledge_base_id}")

    async def retrieve(self, sub_query: str, knowledge_base_id: str) -> list[Chunk]:
        collection = self._get_collection(knowledge_base_id)
        results = collection.query(
            query_texts=[sub_query],
            n_results=4,
            where={"knowledge_base_id": knowledge_base_id},
        )
        ids = results.get("ids", [[]])[0]
        documents = results.get("documents", [[]])[0]
        return [Chunk(chunk_id=chunk_id, content=content) for chunk_id, content in zip(ids, documents)]


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
        llm = LLMFactory().get_model()
        response = await llm.ainvoke(messages)
        content = response.content
        return content if isinstance(content, str) else str(content)


class MultiHopBranch:
    """`agentic_subgraph` 的實作。"""

    router_version = ROUTER_VERSION

    def __init__(
        self,
        decomposer: QueryDecomposer | None = None,
        retriever: HopRetriever | None = None,
        synthesizer: Synthesizer | None = None,
        recursion_limit: int = 4,
        max_cost_units: int = 20_000,
        global_timeout_seconds: float = 30.0,
    ):
        self._decomposer = decomposer or LLMQueryDecomposer()
        self._retriever = retriever or ChromaHopRetriever()
        self._synthesizer = synthesizer or LLMMultiHopSynthesizer()
        self._recursion_limit = recursion_limit
        self._max_cost_units = max_cost_units
        self._global_timeout_seconds = global_timeout_seconds

    async def __call__(self, state: RoutedAgentState) -> dict:
        knowledge_base_id = enforce_knowledge_base_id(state)
        query = state.resolved_query

        try:
            hops, guardrail_hits = await asyncio.wait_for(
                self._run_hops(query, knowledge_base_id),
                timeout=self._global_timeout_seconds,
            )
        except asyncio.TimeoutError:
            hops, guardrail_hits = [], ["global_timeout"]

        if hops:
            answer = await self._synthesizer.synthesize(query, hops)
        else:
            answer = "多跳查詢在護欄限制內沒有取得足夠資訊，無法完整回答。"

        return {
            "answer": answer,
            "documents": [chunk for hop in hops for chunk in hop.chunks],
            "trace": state.trace
            + [f"multi_hop: hops={len(hops)} kb={knowledge_base_id} guardrails={guardrail_hits}"],
        }

    async def _run_hops(
        self, query: str, knowledge_base_id: str
    ) -> tuple[list[HopResult], list[str]]:
        hops: list[HopResult] = []
        asked: set[str] = set()
        cost_units = 0
        guardrail_hits: list[str] = []

        for _ in range(self._recursion_limit):
            sub_queries = await self._decomposer.decompose(query, hops)
            new_sub_queries = [q for q in sub_queries if _normalize(q) not in asked]
            if not new_sub_queries:
                break

            for sub_query in new_sub_queries:
                asked.add(_normalize(sub_query))
                chunks = await self._retriever.retrieve(sub_query, knowledge_base_id)
                cost_units += _estimate_cost(sub_query, chunks)
                hops.append(HopResult(sub_query=sub_query, chunks=chunks))
                if cost_units >= self._max_cost_units:
                    guardrail_hits.append("cost_cap")
                    return hops, guardrail_hits
        else:
            guardrail_hits.append("recursion_limit")

        return hops, guardrail_hits


agentic_subgraph = MultiHopBranch()
