import asyncio
import threading

from langchain_core.documents import Document
from langchain_core.runnables import RunnableLambda

from app.rag.retriever import hybrid_retriever
from app.rag.retriever.hybrid_retriever import HybridRetriever


def test_bm25_build_runs_off_the_event_loop_thread(monkeypatch):
    build_threads: list[int] = []

    class _FakeIndexer:
        def get_all_documents_in_kb(self, tenant_id, user_id, knowledge_base_id):
            build_threads.append(threading.get_ident())
            return [
                Document(id="1", page_content="apple banana"),
                Document(id="2", page_content="cherry durian"),
                Document(id="3", page_content="apple cherry"),
            ]

    monkeypatch.setattr(hybrid_retriever, "get_vector_store_indexer", lambda: _FakeIndexer())
    monkeypatch.setattr(
        hybrid_retriever, "get_collection_retriever", lambda *args, **kwargs: RunnableLambda(lambda q: [])
    )

    async def run():
        loop_thread = threading.get_ident()
        retriever = await HybridRetriever().get_retriever("t", "u", "kb", top_k=2)
        docs = await retriever.ainvoke("apple")
        return loop_thread, docs

    loop_thread, docs = asyncio.run(run())

    assert build_threads and build_threads[0] != loop_thread
    assert len(docs) == 2
