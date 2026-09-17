import asyncio

from langchain_community.retrievers import BM25Retriever
from langchain_classic.retrievers import EnsembleRetriever

from app.rag.retriever.base import RetrieverFactory
from app.rag.retriever.common import get_collection_retriever
from app.vectorstore.StoreIndexer import get_vector_store_indexer
from langchain_core.runnables import Runnable, RunnableLambda

class HybridRetriever(RetrieverFactory):
    def __init__(self, weights: tuple[float, float] = (0.5, 0.5)):
        self.weights = weights
    def _build_bm25(self, tenant_id: str, user_id: str, knowledge_base_id: str):
        indexer = get_vector_store_indexer()
        documents = indexer.get_all_documents_in_kb(tenant_id, user_id, knowledge_base_id)
        return BM25Retriever.from_documents(documents)
    async def get_retriever(
        self, tenant_id: str, user_id: str, knowledge_base_id: str, top_k: int = 8
    ) -> Runnable:
        # Fetching the whole KB from Chroma and tokenizing it for BM25 are both blocking;
        # run them off the event loop so one large KB does not stall the whole process.
        # Pass the function, not its result: `to_thread(fn(...))` would still run fn here.
        bm25_retriever = await asyncio.to_thread(self._build_bm25, tenant_id, user_id, knowledge_base_id)
        bm25_retriever.k = top_k
        dense_retriever = get_collection_retriever(tenant_id, user_id, knowledge_base_id, top_k=top_k, search_type="similarity")
        ensemble_retriever = EnsembleRetriever(
            retrievers=[bm25_retriever, dense_retriever],
            weights=self.weights,
        )
        return ensemble_retriever | RunnableLambda(lambda docs: docs[:top_k])
