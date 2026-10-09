import uuid

import chromadb
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import DeterministicFakeEmbedding

from app.vectorstore.StoreIndexer import StoreIndexer


class _SharedCollectionManager:
    """Every (user, tenant) maps to one in-memory collection, i.e. PARTITION_METHOD=1:
    the worst case, where the metadata filter is the only isolation left."""

    def __init__(self):
        self.collection = Chroma(
            client=chromadb.EphemeralClient(),
            collection_name=f"test_{uuid.uuid4().hex}",
            embedding_function=DeterministicFakeEmbedding(size=8),
        )

    def get_or_create_collection(self, user_id: str, tenant_id: str) -> Chroma:
        return self.collection


def _indexer_with_two_tenants() -> StoreIndexer:
    indexer = StoreIndexer(collection_manager=_SharedCollectionManager())
    for tenant, user, kb in [("t1", "u1", "kb"), ("t2", "u2", "kb"), ("t1", "u1", "kb-other")]:
        indexer.add_documents(
            [Document(page_content=f"apple from {tenant}/{user}/{kb}")],
            tenant_id=tenant,
            user_id=user,
            metadata={"tenant_id": tenant, "user_id": user, "knowledge_base_id": kb},
        )
    return indexer


def test_scope_filter_covers_all_three_ids():
    where = StoreIndexer.scope_filter("t", "u", "kb")

    assert where == {"$and": [
        {"knowledge_base_id": "kb"},
        {"user_id": "u"},
        {"tenant_id": "t"},
    ]}


def test_extra_filter_is_appended_not_substituted():
    indexer = StoreIndexer(collection_manager=_SharedCollectionManager())

    retriever = indexer.get_dense_retriever("t", "u", "kb", top_k=3, extra_filter={"source": "a.pdf"})

    conditions = retriever.search_kwargs["filter"]["$and"]
    assert conditions[:3] == StoreIndexer.scope_filter("t", "u", "kb")["$and"]
    assert conditions[3] == {"source": "a.pdf"}
    assert retriever.search_kwargs["k"] == 3


def test_dense_and_sparse_paths_see_the_same_scope():
    indexer = _indexer_with_two_tenants()

    dense = indexer.get_dense_retriever("t1", "u1", "kb", top_k=10).invoke("apple")
    sparse = indexer.get_all_documents_in_kb("t1", "u1", "kb")

    assert [d.page_content for d in dense] == ["apple from t1/u1/kb"]
    assert [d.page_content for d in sparse] == ["apple from t1/u1/kb"]


def test_same_kb_id_in_another_tenant_is_not_visible():
    indexer = _indexer_with_two_tenants()

    dense = indexer.get_dense_retriever("t2", "u2", "kb", top_k=10).invoke("apple")

    assert [d.page_content for d in dense] == ["apple from t2/u2/kb"]
