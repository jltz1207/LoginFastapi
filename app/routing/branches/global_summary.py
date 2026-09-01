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

from app.routing.branches.common import Chunk, enforce_knowledge_base_id

if TYPE_CHECKING:  # type-only: keeps `routing` free of a runtime import on `agent`
    from app.agent.state import RoutedAgentState

ROUTER_VERSION = "global-summary-v1"

_NO_SUMMARIES_MESSAGE = "這個知識庫目前沒有可用的摘要索引，請先確認 ingestion pipeline 是否已產生 document summary。"


class SummaryIndexClient(Protocol):
    async def fetch_document_summaries(self, knowledge_base_id: str) -> list[Chunk]: ...


class MapReduceLLM(Protocol):
    async def complete(self, prompt: str) -> str: ...


class ChromaSummaryIndexClient:
    """預設實作：Celery ingestion 把每份文件摘要寫進 Chroma 的獨立 summary collection。"""

    def __init__(self, collection=None):
        self._collection = collection

    def _get_collection(self, knowledge_base_id: str):
        if self._collection is None:
            import chromadb

            client = chromadb.Client()
            self._collection = client.get_or_create_collection(f"kb_{knowledge_base_id}_summaries")
        return self._collection

    async def fetch_document_summaries(self, knowledge_base_id: str) -> list[Chunk]:
        collection = self._get_collection(knowledge_base_id)
        results = collection.get(where={"knowledge_base_id": knowledge_base_id})
        ids = results.get("ids", [])
        documents = results.get("documents", [])
        metadatas = results.get("metadatas", []) or [{}] * len(ids)
        return [
            Chunk(chunk_id=doc_id, content=content, source=(metadata or {}).get("source", doc_id), metadata=metadata or {})
            for doc_id, content, metadata in zip(ids, documents, metadatas)
        ]


class GeminiMapReduceLLM:
    def __init__(self, model=None):
        self._model = model

    def _get_model(self):
        if self._model is None:
            from langchain_google_genai import ChatGoogleGenerativeAI

            self._model = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.2)
        return self._model

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
    """`global_summary_node` 的實作（修正原本 `lobal_summary_node` 的命名 typo）。"""

    router_version = ROUTER_VERSION

    def __init__(
        self,
        index_client: SummaryIndexClient | None = None,
        llm: MapReduceLLM | None = None,
        batch_size: int = 8,
    ):
        self._index_client = index_client or ChromaSummaryIndexClient()
        self._llm = llm or GeminiMapReduceLLM()
        self._batch_size = batch_size

    async def __call__(self, state: RoutedAgentState) -> dict:
        knowledge_base_id = enforce_knowledge_base_id(state)
        query = state.resolved_query

        summaries = await self._index_client.fetch_document_summaries(knowledge_base_id)
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
