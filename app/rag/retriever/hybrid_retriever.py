from langchain_community.retrievers import BM25Retriever
from langchain_classic.retrievers import EnsembleRetriever

from app.rag.retriever.base import RetrieverFactory
from app.rag.retriever.common import get_collection_retriever
from app.vectorstore.StoreIndexer import get_vector_store_indexer
from langchain_core.runnables import Runnable, RunnableLambda

class HybridRetriever(RetrieverFactory):
    def __init__(self, weights: tuple[float, float] = (0.5, 0.5)):
        self.weights = weights
    async def get_retriever(
        self, tenant_id: str, user_id: str, knowledge_base_id: str, top_k: int = 8
    ) -> Runnable:
        indexer = get_vector_store_indexer()
        documents = indexer.get_all_documents_in_kb(tenant_id, user_id, knowledge_base_id)
        bm25_retriever = BM25Retriever.from_documents(documents)
        bm25_retriever.k = top_k
        dense_retriever = get_collection_retriever(tenant_id, user_id, knowledge_base_id, top_k=top_k, search_type="similarity")
        ensemble_retriever = EnsembleRetriever(
            retrievers=[bm25_retriever, dense_retriever],
            weights=self.weights,
        )
        return ensemble_retriever | RunnableLambda(lambda docs: docs[:top_k])
