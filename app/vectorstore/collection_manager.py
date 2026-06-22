from app.rag.embeddings.embedding_factory import EmbeddingFactory
from app.vectorstore.client import get_chroma_db
from app.core import settings
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_chroma import Chroma
from enum import Enum

class vector_partition_strategy(Enum):
     GLOBAL_COLLECTION = 1
     ONE_USER_ONE_COLLECTION = 2


class Collection_manager():
    def __init__(self, client = None):
            self.client = client or get_chroma_db().client
            self.embedding_function = EmbeddingFactory.get_embedding_function()
            self.partition_strategy = vector_partition_strategy(settings.PARTITION_METHOD)
    
    def get_or_create_collection(self, user_id:str) -> Chroma:
        if self.partition_strategy == vector_partition_strategy.ONE_USER_ONE_COLLECTION:
            vector_store = Chroma(
            client=self.client,
            collection_name=f'{settings.COLLECTION_NAME}_{user_id}',
            embedding_function=self.embedding_function,
            )
        else: # global
            vector_store = Chroma(
            client=self.client,
            collection_name=settings.COLLECTION_NAME,
            embedding_function=self.embedding_function,
            )
        return vector_store

