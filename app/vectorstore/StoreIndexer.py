from functools import lru_cache

from langchain_core.documents import Document
from app.vectorstore.collection_manager import Collection_manager

class StoreIndexer:
    def __init__(self):
        self.collections = Collection_manager()

    def add_documents(self, docs: list[Document], user_id: str, metadata: dict | None = None):
        metadata = metadata or {}
        user_collection = self.collections.get_or_create_collection(user_id)
        user_collection.add_documents([Document(page_content = doc.page_content, metadata= metadata | doc.metadata) for doc in docs])
    
    def query(self, query: str, user_id: str):
        user_collection = self.collections.get_or_create_collection(user_id)
        user_collection.query

@lru_cache(maxsize=1)
def get_vector_store_indexer() -> StoreIndexer:
    return StoreIndexer()