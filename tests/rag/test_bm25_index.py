from langchain_core.documents import Document

from app.rag.retriever.bm25_index import BM25IndexProvider, CachedBM25Retriever

_DOCS = [
    Document(id="1", page_content="apple banana"),
    Document(id="2", page_content="cherry durian"),
    Document(id="3", page_content="apple cherry"),
    Document(id="4", page_content="elder fig"),
]


class _CountingLoader:
    def __init__(self, documents=_DOCS):
        self.documents = documents
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, tenant_id, user_id, knowledge_base_id):
        self.calls.append((tenant_id, user_id, knowledge_base_id))
        return self.documents


def test_cache_hit_does_not_reload():
    loader = _CountingLoader()
    provider = BM25IndexProvider(load_documents=loader)

    first = provider.get("t", "u", "kb")
    second = provider.get("t", "u", "kb")

    assert first is second
    assert len(loader.calls) == 1


def test_invalidate_forces_rebuild():
    loader = _CountingLoader()
    provider = BM25IndexProvider(load_documents=loader)

    provider.get("t", "u", "kb")
    provider.invalidate("t", "u", "kb")
    provider.get("t", "u", "kb")

    assert len(loader.calls) == 2


def test_keys_are_isolated_by_full_tenant_scope():
    loader = _CountingLoader()
    provider = BM25IndexProvider(load_documents=loader)

    provider.get("tenant-a", "u", "kb")
    provider.get("tenant-b", "u", "kb")
    provider.get("tenant-a", "other-user", "kb")
    provider.invalidate("tenant-b", "u", "kb")
    provider.get("tenant-a", "u", "kb")

    assert loader.calls == [("tenant-a", "u", "kb"), ("tenant-b", "u", "kb"), ("tenant-a", "other-user", "kb")]


def test_invalidate_during_build_does_not_cache_stale_index():
    provider: BM25IndexProvider

    class _UploadLandsMidBuild(_CountingLoader):
        def __call__(self, tenant_id, user_id, knowledge_base_id):
            result = super().__call__(tenant_id, user_id, knowledge_base_id)
            if len(self.calls) == 1:
                provider.invalidate(tenant_id, user_id, knowledge_base_id)
            return result

    loader = _UploadLandsMidBuild()
    provider = BM25IndexProvider(load_documents=loader)

    assert provider.get("t", "u", "kb") is not None
    provider.get("t", "u", "kb")

    assert len(loader.calls) == 2


def test_least_recently_used_index_is_evicted():
    loader = _CountingLoader()
    provider = BM25IndexProvider(load_documents=loader, max_indexes=2)

    provider.get("t", "u", "kb-1")
    provider.get("t", "u", "kb-2")
    provider.get("t", "u", "kb-1")  # kb-1 is now most recently used
    provider.get("t", "u", "kb-3")  # evicts kb-2
    provider.get("t", "u", "kb-1")
    provider.get("t", "u", "kb-2")

    assert [key[2] for key in loader.calls] == ["kb-1", "kb-2", "kb-3", "kb-2"]


def test_empty_knowledge_base_is_cached_as_none():
    loader = _CountingLoader(documents=[])
    provider = BM25IndexProvider(load_documents=loader)

    assert provider.get("t", "u", "kb") is None
    assert provider.get("t", "u", "kb") is None
    assert len(loader.calls) == 1


def test_retrievers_with_different_k_share_one_index_without_mutating_it():
    loader = _CountingLoader()
    provider = BM25IndexProvider(load_documents=loader)
    narrow = CachedBM25Retriever(tenant_id="t", user_id="u", knowledge_base_id="kb", k=1, provider=provider)
    wide = CachedBM25Retriever(tenant_id="t", user_id="u", knowledge_base_id="kb", k=3, provider=provider)

    shared_k_before = provider.get("t", "u", "kb").k

    assert len(narrow.invoke("apple")) == 1
    assert len(wide.invoke("apple")) == 3
    assert len(narrow.invoke("apple")) == 1
    assert provider.get("t", "u", "kb").k == shared_k_before
    assert len(loader.calls) == 1
