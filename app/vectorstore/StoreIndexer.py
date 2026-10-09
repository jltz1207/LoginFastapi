from functools import lru_cache

from langchain_core.documents import Document
from langchain_core.vectorstores import VectorStoreRetriever
from app.vectorstore.collection_manager import Collection_manager

class StoreIndexer:
    def __init__(self, collection_manager: Collection_manager | None = None):
        self.collection_manager = collection_manager or Collection_manager()

    @staticmethod
    def scope_filter(tenant_id: str, user_id: str, knowledge_base_id: str) -> dict:
        # Single source of truth for the tenant boundary. Both the dense path
        # (get_dense_retriever) and the BM25 path (get_all_documents_in_kb) must go
        # through this; if they filtered differently, the sparse branch could surface
        # documents the dense branch is not allowed to see.
        return {"$and": [
            {"knowledge_base_id": knowledge_base_id},
            {"user_id": user_id},
            {"tenant_id": tenant_id},
        ]}

    def add_documents(self, docs: list[Document], tenant_id: str, user_id: str, metadata: dict | None = None):
        metadata = metadata or {}
        user_collection = self.collection_manager.get_or_create_collection(user_id=user_id, tenant_id=tenant_id)
        user_collection.add_documents([Document(page_content = doc.page_content, metadata= metadata | doc.metadata) for doc in docs])

    def retrieve(self, query: str, tenant_id: str, user_id: str):
        user_collection = self.collection_manager.get_or_create_collection(user_id=user_id, tenant_id=tenant_id)
        user_collection.query

    def get_dense_retriever(
        self,
        tenant_id: str,
        user_id: str,
        knowledge_base_id: str,
        *,
        top_k: int = 8,
        search_type: str = "similarity",
    ) -> VectorStoreRetriever:
        # No extra filter on purpose: the BM25 half of HybridRetriever caches one index
        # per scope and cannot apply it, so a dense-only filter would make the two halves
        # disagree. Add one only together with BM25 support.
        user_collection = self.collection_manager.get_or_create_collection(user_id=user_id, tenant_id=tenant_id)
        return user_collection.as_retriever(
            search_type=search_type,
            search_kwargs={"k": top_k, "filter": self.scope_filter(tenant_id, user_id, knowledge_base_id)},
        )

    def get_all_documents_in_kb(self, tenant_id: str, user_id: str, kb_id: str) -> list[Document]:
        user_collection = self.collection_manager.get_or_create_collection(user_id=user_id, tenant_id=tenant_id)
        raw = user_collection.get(
            where=self.scope_filter(tenant_id, user_id, kb_id),
            include=["documents", "metadatas"]
            )
        documents= [Document(page_content=doc, metadata=metadata) for doc, metadata in zip(raw["documents"], raw["metadatas"])]
        return documents

@lru_cache(maxsize=1)
def get_vector_store_indexer() -> StoreIndexer:
    return StoreIndexer()
