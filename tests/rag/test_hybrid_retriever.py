import asyncio
import threading

from langchain_core.documents import Document
from langchain_core.runnables import RunnableLambda

from app.rag.retriever.bm25_index import BM25IndexProvider
from app.rag.retriever.hybrid_retriever import HybridRetriever

_DOCS = [
    Document(id="1", page_content="apple banana"),
    Document(id="2", page_content="cherry durian"),
    Document(id="3", page_content="apple cherry"),
]


class _EmptyDenseIndexer:
    def get_dense_retriever(self, *args, **kwargs):
        return RunnableLambda(lambda q: [])


def test_get_retriever_is_pure_assembly():
    loads: list[tuple[str, str, str]] = []
    provider = BM25IndexProvider(load_documents=lambda *key: loads.append(key) or _DOCS)

    HybridRetriever(bm25_indexes=provider, indexer=_EmptyDenseIndexer()).get_retriever("t", "u", "kb", top_k=2)

    assert loads == []


def test_bm25_build_runs_off_the_event_loop_thread():
    build_threads: list[int] = []

    def load_documents(tenant_id, user_id, knowledge_base_id):
        build_threads.append(threading.get_ident())
        return _DOCS

    provider = BM25IndexProvider(load_documents=load_documents)

    async def run():
        loop_thread = threading.get_ident()
        retriever = HybridRetriever(bm25_indexes=provider, indexer=_EmptyDenseIndexer()).get_retriever("t", "u", "kb", top_k=2)
        docs = await retriever.ainvoke("apple")
        return loop_thread, docs

    loop_thread, docs = asyncio.run(run())

    assert build_threads and build_threads[0] != loop_thread
    assert len(docs) == 2


def test_empty_knowledge_base_does_not_raise():
    provider = BM25IndexProvider(load_documents=lambda *key: [])
    retriever = HybridRetriever(bm25_indexes=provider, indexer=_EmptyDenseIndexer()).get_retriever("t", "u", "kb", top_k=2)

    assert asyncio.run(retriever.ainvoke("apple")) == []
