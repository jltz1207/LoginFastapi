"""BM25 index 的 process 內 cache。

BM25 沒辦法建在 Chroma 裡，只能把整個 KB 的 chunk 撈回記憶體再 tokenize、建倒排索引。
這筆成本在文件沒變之前每次都一樣，所以依 `(tenant_id, user_id, knowledge_base_id)`
cache 起來。失效有兩層：

- 經過 `StoreIndexer` 的寫入會透過 `app/vectorstore/events.py` 自動呼叫 `invalidate()`
  （在寫入的同一個 thread、寫入之後），寫入端不必自己記得。
- 收不到通知的寫入（其他 process、其他 worker、直接寫 chromadb 的腳本）靠 TTL 保底：
  cache 超過 `ttl_seconds` 就重建。

建置刻意延到「檢索被 invoke 時」才發生（`CachedBM25Retriever`），而不是
`get_retriever()` 組裝時：`BaseRetriever` 的 async 路徑會把 sync 實作丟進 executor，
cache miss 的重活因此自然離開 event loop，`get_retriever()` 則只剩純組裝、可以是 sync。

已知限制：
- cache 只存在單一 process。多 worker 或外部 process 的寫入最多延遲 TTL 才反映。
- 任何會寫入或刪除 KB chunk 的路徑都必須經過 `StoreIndexer`，才會自動失效。
"""

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from functools import lru_cache

from langchain_community.retrievers import BM25Retriever
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import Field

from app.vectorstore.StoreIndexer import get_vector_store_indexer
from app.vectorstore.events import on_kb_changed

IndexKey = tuple[str, str, str]
DocumentLoader = Callable[[str, str, str], list[Document]]

# Each cached index holds the full text of its KB in memory, so the cache must be bounded.
DEFAULT_MAX_INDEXES = 64
# Safety net for writes this process is never told about. Not measured: shorter recovers
# sooner but rebuilds large KBs (load all chunks + tokenize) more often.
DEFAULT_TTL_SECONDS = 600.0


def _load_documents_from_vector_store(tenant_id: str, user_id: str, knowledge_base_id: str) -> list[Document]:
    return get_vector_store_indexer().get_all_documents_in_kb(tenant_id, user_id, knowledge_base_id)


class BM25IndexProvider:
    """Thread-safe LRU cache of BM25 indexes, keyed by tenant scope.

    `get()` runs on executor threads (see module docstring), hence `threading.Lock`
    rather than `asyncio.Lock`.
    """

    def __init__(
        self,
        load_documents: DocumentLoader | None = None,
        max_indexes: int = DEFAULT_MAX_INDEXES,
        ttl_seconds: float | None = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._load_documents = load_documents or _load_documents_from_vector_store
        self._max_indexes = max_indexes
        self._ttl_seconds = ttl_seconds  # None: never expires
        self._clock = clock
        # Value is (index, built_at). `None` is a cached index too: it means the KB is empty.
        self._indexes: OrderedDict[IndexKey, tuple[BM25Retriever | None, float]] = OrderedDict()
        self._generations: dict[IndexKey, int] = {}
        self._lock = threading.Lock()

    def get(self, tenant_id: str, user_id: str, knowledge_base_id: str) -> BM25Retriever | None:
        """回傳該 KB 的 BM25 index；KB 沒有任何 chunk 時回傳 `None`。cache miss 時會同步建置。"""
        # All three ids, not just knowledge_base_id: see StoreIndexer.get_all_documents_in_kb.
        key = (tenant_id, user_id, knowledge_base_id)
        with self._lock:
            if key in self._indexes:
                index, built_at = self._indexes[key]
                if self._ttl_seconds is None or self._clock() - built_at <= self._ttl_seconds:
                    self._indexes.move_to_end(key)
                    return index
                del self._indexes[key]
            generation = self._generations.get(key, 0)

        # Built outside the lock so one large KB does not stall lookups for every other KB.
        # Concurrent misses on the same key may each build once; the result is identical,
        # so that is accepted rather than adding per-key locking.
        # Stamped before loading: the index can only be as fresh as the moment its load began.
        built_at = self._clock()
        documents = self._load_documents(tenant_id, user_id, knowledge_base_id)
        # BM25Retriever.from_documents raises on an empty list (it unpacks zip(*docs)).
        index = BM25Retriever.from_documents(documents) if documents else None

        with self._lock:
            # An invalidate() that landed while we were building means `documents` may
            # predate a write. Serve it to this caller, but do not cache it.
            if self._generations.get(key, 0) == generation:
                self._indexes[key] = (index, built_at)
                self._indexes.move_to_end(key)
                while len(self._indexes) > self._max_indexes:
                    self._indexes.popitem(last=False)
        return index

    def invalidate(self, tenant_id: str, user_id: str, knowledge_base_id: str) -> None:
        key = (tenant_id, user_id, knowledge_base_id)
        with self._lock:
            self._indexes.pop(key, None)
            self._generations[key] = self._generations.get(key, 0) + 1


@lru_cache(maxsize=1)
def get_bm25_index_provider() -> BM25IndexProvider:
    provider = BM25IndexProvider()
    # Writes through StoreIndexer invalidate automatically. Before the provider exists
    # there is no cache, so writes made earlier need no notification.
    on_kb_changed(provider.invalidate)
    return provider


class CachedBM25Retriever(BaseRetriever):
    """BM25 retriever whose index is fetched from `BM25IndexProvider` at query time.

    Only the tenant scope and `k` live on this object, so constructing it is free.
    """

    tenant_id: str
    user_id: str
    knowledge_base_id: str
    k: int = 4
    provider: BM25IndexProvider = Field(default_factory=get_bm25_index_provider)

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        index = self.provider.get(self.tenant_id, self.user_id, self.knowledge_base_id)
        if index is None:
            return []
        # The cached index is shared across requests, so never mutate its `k`.
        # model_copy is shallow: the vectorizer and docs are shared, not copied.
        return index.model_copy(update={"k": self.k}).invoke(
            query, config={"callbacks": run_manager.get_child()}
        )
