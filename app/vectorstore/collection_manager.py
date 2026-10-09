import threading
from collections import OrderedDict

from app.rag.embeddings.embedding_factory import EmbeddingFactory
from app.vectorstore.client import get_chroma_db
from app.core import settings
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_chroma import Chroma
from enum import Enum

# Each entry is only a handle (the HNSW index lives in chromadb), but the key space
# grows with users/tenants, so the cache must still be bounded.
DEFAULT_MAX_COLLECTIONS = 256

class vector_partition_strategy(Enum):
     GLOBAL_COLLECTION = 1
     ONE_USER_ONE_COLLECTION = 2
     ONE_TENANT_ONE_COLLECTION = 3

class Collection_manager():
    def __init__(
        self,
        client=None,
        embedding_function=None,
        partition_strategy: vector_partition_strategy | None = None,
        max_collections: int = DEFAULT_MAX_COLLECTIONS,
    ):
        self.client = client or get_chroma_db().client
        self.embedding_function = (
            embedding_function if embedding_function is not None else EmbeddingFactory.get_embedding_function()
        )
        self.partition_strategy = partition_strategy or vector_partition_strategy(settings.PARTITION_METHOD)
        self._max_collections = max_collections
        self._cache: OrderedDict[str, Chroma] = OrderedDict()
        # get_or_create_collection() is reached from executor threads (BM25 loader,
        # upload writes) as well as the event loop thread.
        self._lock = threading.Lock()

    def _collection_name(self, user_id: str, tenant_id: str) -> str:
        # The actual Chroma collection name, used as the cache key too, so one physical
        # collection maps to exactly one cached handle under every strategy.
        if self.partition_strategy == vector_partition_strategy.ONE_USER_ONE_COLLECTION:
            return f"{settings.COLLECTION_NAME}_{user_id}"
        if self.partition_strategy == vector_partition_strategy.ONE_TENANT_ONE_COLLECTION:
            return f"{settings.COLLECTION_NAME}_{tenant_id}"
        return settings.COLLECTION_NAME

    def get_or_create_collection(self, user_id: str, tenant_id: str) -> Chroma:
        collection_name = self._collection_name(user_id, tenant_id)
        with self._lock:
            if collection_name in self._cache:
                self._cache.move_to_end(collection_name)
                return self._cache[collection_name]

        # Built outside the lock: Chroma() does a sysdb round-trip. Concurrent misses on
        # the same name may each build a handle; they are equivalent, the last one wins.
        vector_store = Chroma(
            client=self.client,
            collection_name=collection_name,
            embedding_function=self.embedding_function,
        )
        with self._lock:
            self._cache[collection_name] = vector_store
            self._cache.move_to_end(collection_name)
            while len(self._cache) > self._max_collections:
                self._cache.popitem(last=False)
        return vector_store

    def evict(self, user_id: str, tenant_id: str) -> None:
        """Drop the cached handle. Any code path that deletes a collection must call this,
        or later lookups keep serving a handle to a collection that no longer exists."""
        with self._lock:
            self._cache.pop(self._collection_name(user_id, tenant_id), None)
