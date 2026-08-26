from functools import lru_cache

from langchain_core.documents import Document
from app.vectorstore.collection_manager import Collection_manager

class StoreIndexer:
    def __init__(self):
        self.collection_manager = Collection_manager()

    def add_documents(self, docs: list[Document], user_id: str, metadata: dict | None = None):
        metadata = metadata or {}
        user_collection = self.collection_manager.get_or_create_collection(user_id)
        user_collection.add_documents([Document(page_content = doc.page_content, metadata= metadata | doc.metadata) for doc in docs])
    
    def query(self, query: str, user_id: str):
        user_collection = self.collection_manager.get_or_create_collection(user_id)
        user_collection.query

    def get_all_documents_in_kb(self, user_id: str, kb_id: str) -> list[Document]:
        user_collection = self.collection_manager.get_or_create_collection(user_id)
        raw = user_collection.get(
            where={"knowledge_base_id": kb_id},
            include=["documents", "metadatas"]
            )
        documents= [Document(page_content=doc, metadata=metadata) for doc, metadata in zip(raw["documents"], raw["metadatas"])]
        return documents

@lru_cache(maxsize=1)
def get_vector_store_indexer() -> StoreIndexer:
    return StoreIndexer()