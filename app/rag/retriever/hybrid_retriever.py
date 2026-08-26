from langchain_community.retrievers import BM25Retriever
from langchain_classic.retrievers import EnsembleRetriever

from app.rag.retriever.base import RetrieverFactory
from app.rag.retriever.basic_retriever import get_collection_retriever
from app.vectorstore.StoreIndexer import get_vector_store_indexer
from langchain_core.runnables import Runnable

class HybridRetriever(RetrieverFactory):
    def __init__(self, weights: tuple[float] = (0.5, 0.5)):
        self.weights = weights
    async def get_retriever(
        self, user_id: str, knowledge_base_id: str, top_k: int = 8
    ) -> Runnable:
        indexer = get_vector_store_indexer()
        documents = indexer.get_all_documents_in_kb(user_id, knowledge_base_id)
        bm25_retriever = BM25Retriever.from_documents(documents)
        dense_retriever = get_collection_retriever(user_id, knowledge_base_id, top_k=top_k, search_type="similarity")
        ensemble_retriever = EnsembleRetriever(
            retrievers=[bm25_retriever, dense_retriever],
            weights=self.weights,
        )
        return ensemble_retriever