# from langchain_core.retrievers import BaseRetriever

from app.rag.retriever.base import RetrieverFactory
from app.rag.retriever.common import get_collection_retriever


class BasicRetriever(RetrieverFactory):
    async def get_retriever(
        self, user_id: str, knowledge_base_id: str, top_k: int = 8
    ):
        return get_collection_retriever(user_id, knowledge_base_id, top_k=top_k, search_type="similarity")

