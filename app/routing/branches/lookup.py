"""LOOKUP 分支（default route）：hybrid retrieval（Chroma）-> rerank -> 生成 + 引用。

租戶隔離：`knowledge_base_id` 一律用 `common.enforce_knowledge_base_id()` 從
state 強制取出，不信任 router 輸出的 `state["filters"]`。
"""

from __future__ import annotations

from typing import Protocol

from routing.branches.common import Chunk, enforce_knowledge_base_id, format_citations

ROUTER_VERSION = "lookup-v1"


class HybridRetriever(Protocol):
    async def retrieve(
        self, query: str, knowledge_base_id: str, top_k: int = 8
    ) -> list[Chunk]: ...


class Reranker(Protocol):
    async def rerank(
        self, query: str, chunks: list[Chunk], top_k: int = 4
    ) -> list[Chunk]: ...


class AnswerGenerator(Protocol):
    async def generate(self, query: str, chunks: list[Chunk]) -> str: ...


class ChromaHybridRetriever:
    """預設檢索器：對 Chroma per-user collection 做 hybrid retrieval，
    query 一律連同 `knowledge_base_id` metadata filter 一起送出，
    絕不允許沒有這個 filter 的查詢打到向量庫。
    """

    def __init__(self, collection=None):
        self._collection = collection

    def _get_collection(self, knowledge_base_id: str):
        if self._collection is None:
            import chromadb

            client = chromadb.Client()
            self._collection = client.get_or_create_collection(f"kb_{knowledge_base_id}")
        return self._collection

    async def retrieve(
        self, query: str, knowledge_base_id: str, top_k: int = 8
    ) -> list[Chunk]:
        collection = self._get_collection(knowledge_base_id)
        results = collection.query(
            query_texts=[query],
            n_results=top_k,
            where={"knowledge_base_id": knowledge_base_id},
        )
        ids = results.get("ids", [[]])[0]
        documents = results.get("documents", [[]])[0]
        distances = results.get("distances", [[]])[0] if results.get("distances") else [0.0] * len(ids)
        metadatas = results.get("metadatas", [[]])[0] if results.get("metadatas") else [{}] * len(ids)
        return [
            Chunk(
                chunk_id=chunk_id,
                content=content,
                source=(metadata or {}).get("source", chunk_id),
                score=1.0 - distance,
                metadata=metadata or {},
            )
            for chunk_id, content, distance, metadata in zip(ids, documents, distances, metadatas)
        ]


class PassthroughReranker:
    """沒有接真正 rerank 模型時的預設實作：照原始檢索分數排序、截斷 top_k。"""

    async def rerank(self, query: str, chunks: list[Chunk], top_k: int = 4) -> list[Chunk]:
        return sorted(chunks, key=lambda c: c.score, reverse=True)[:top_k]


class GeminiAnswerGenerator:
    def __init__(self, model=None):
        self._model = model

    def _get_model(self):
        if self._model is None:
            from langchain_google_genai import ChatGoogleGenerativeAI

            self._model = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.2)
        return self._model

    async def generate(self, query: str, chunks: list[Chunk]) -> str:
        context = "\n\n".join(f"[{i + 1}] {c.content}" for i, c in enumerate(chunks))
        messages = [
            ("system", "根據提供的段落回答使用者問題，只根據段落內容作答，並在句尾用 [編號] 標註引用來源。"),
            ("human", f"段落：\n{context}\n\n問題：{query}"),
        ]
        response = await self._get_model().ainvoke(messages)
        content = response.content
        return content if isinstance(content, str) else str(content)


class LookupBranch:
    """`standard_rag_node` 的實作。"""

    router_version = ROUTER_VERSION

    def __init__(
        self,
        retriever: HybridRetriever | None = None,
        reranker: Reranker | None = None,
        generator: AnswerGenerator | None = None,
        top_k: int = 8,
        rerank_top_k: int = 4,
    ):
        self._retriever = retriever or ChromaHybridRetriever()
        self._reranker = reranker or PassthroughReranker()
        self._generator = generator or GeminiAnswerGenerator()
        self._top_k = top_k
        self._rerank_top_k = rerank_top_k

    async def __call__(self, state: dict) -> dict:
        knowledge_base_id = enforce_knowledge_base_id(state)
        query = state["resolved_query"]

        retrieved = await self._retriever.retrieve(
            query, knowledge_base_id=knowledge_base_id, top_k=self._top_k
        )
        reranked = await self._reranker.rerank(query, retrieved, top_k=self._rerank_top_k)
        answer = await self._generator.generate(query, reranked)
        citations = format_citations(reranked)
        if citations:
            answer = f"{answer}\n\n引用來源：\n{citations}"

        return {
            "answer": answer,
            "documents": reranked,
            "trace": state.get("trace", [])
            + [f"lookup: retrieved={len(retrieved)} reranked={len(reranked)} kb={knowledge_base_id}"],
        }


standard_rag_node = LookupBranch()
