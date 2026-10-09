# from langchain_core.retrievers import BaseRetriever

from app.rag.retriever.base import RetrieverFactory
from app.vectorstore.StoreIndexer import get_vector_store_indexer


class BasicRetriever(RetrieverFactory):
    def get_retriever(
        self, tenant_id: str, user_id: str, knowledge_base_id: str, top_k: int = 8
    ):
        return get_vector_store_indexer().get_dense_retriever(
            tenant_id, user_id, knowledge_base_id, top_k=top_k, search_type="similarity"
        )
