from app.rag.embeddings.embedding_factory import EmbeddingFactory
from app.vectorstore.client import get_chroma_db
from app.core import settings
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_chroma import Chroma
from enum import Enum

class vector_partition_strategy(Enum):
     GLOBAL_COLLECTION = 1
     ONE_USER_ONE_COLLECTION = 2
     ONE_TENANT_ONE_COLLECTION = 3

class Collection_manager():
    def __init__(self, client = None):
            self.client = client or get_chroma_db().client
            self.embedding_function = EmbeddingFactory.get_embedding_function()
            self.partition_strategy = vector_partition_strategy(settings.PARTITION_METHOD)
            self.cache: dict[str, Chroma] = {}
    def _collection_name(self, user_id:str, tenant_id:str) -> str:
         return f"{settings.COLLECTION_NAME}_{user_id}_{tenant_id}"
    def get_or_create_collection(self, user_id:str, tenant_id:str) -> Chroma:
        collection_name = self._collection_name(user_id, tenant_id)
        if collection_name in self.cache:
            return self.cache[collection_name]
        if self.partition_strategy == vector_partition_strategy.ONE_USER_ONE_COLLECTION:
            vector_store = Chroma(
            client=self.client,
            collection_name=f'{settings.COLLECTION_NAME}_{user_id}',
            embedding_function=self.embedding_function,
            )
        elif self.partition_strategy == vector_partition_strategy.ONE_TENANT_ONE_COLLECTION:
            vector_store = Chroma(
            client=self.client,
            collection_name=f'{settings.COLLECTION_NAME}_{tenant_id}',
            embedding_function=self.embedding_function,
            )
        else:
            vector_store = Chroma(
                client=self.client,
                collection_name=f'{settings.COLLECTION_NAME}',
                embedding_function=self.embedding_function,
            )
        self.cache[collection_name] = vector_store
        return vector_store

