from langchain_classic.retrievers import EnsembleRetriever

from app.rag.retriever.base import RetrieverFactory
from app.rag.retriever.bm25_index import BM25IndexProvider, CachedBM25Retriever, get_bm25_index_provider
from app.vectorstore.StoreIndexer import StoreIndexer, get_vector_store_indexer
from langchain_core.runnables import Runnable, RunnableLambda

class HybridRetriever(RetrieverFactory):
    def __init__(
        self,
        weights: tuple[float, float] = (0.5, 0.5),
        bm25_indexes: BM25IndexProvider | None = None,
        indexer: StoreIndexer | None = None,
    ):
        self.weights = weights
        self._bm25_indexes = bm25_indexes or get_bm25_index_provider()
        # Resolved lazily in get_retriever(): building the default indexer opens the
        # Chroma client and embedding function, which constructing this object should not do.
        self._indexer = indexer
    def get_retriever(
        self, tenant_id: str, user_id: str, knowledge_base_id: str, top_k: int = 8
    ) -> Runnable:
        # Pure assembly. The BM25 index is fetched (and built on a cache miss) only when
        # the pipeline is invoked; via ainvoke that happens on an executor thread.
        bm25_retriever = CachedBM25Retriever(
            tenant_id=tenant_id,
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
            k=top_k,
            provider=self._bm25_indexes,
        )
        indexer = self._indexer or get_vector_store_indexer()
        dense_retriever = indexer.get_dense_retriever(
            tenant_id, user_id, knowledge_base_id, top_k=top_k, search_type="similarity"
        )
        ensemble_retriever = EnsembleRetriever(
            retrievers=[bm25_retriever, dense_retriever],
            weights=self.weights,
        )
        return ensemble_retriever | RunnableLambda(lambda docs: docs[:top_k])
