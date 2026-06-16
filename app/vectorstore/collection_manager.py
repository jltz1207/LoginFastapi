from app.db import get_chroma_db
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
            self.embedding_function = self.get_embedding_function()
            self.partition_strategy = vector_partition_strategy(settings.partition_method)
    
    def get_embedding_function(self):
        if settings.EMBEDDING_MODEL and settings.GEMINI_API_KEY:
            gemini_ef = GoogleGenerativeAIEmbeddings(
                model = settings.EMBEDDING_MODEL,
                google_api_key=settings.GEMINI_API_KEY
            )
            return gemini_ef
        return None
    
    def get_or_create_collection(self, user_id:str) -> Chroma:
        if self.partition_strategy == vector_partition_strategy.ONE_USER_ONE_COLLECTION:
            vector_store = Chroma(
            client=self.client,
            collection_name=f'kb_{user_id}', 
            embedding_function=self.embedding_function,
            )
        else: # global
            vector_store = Chroma(
            client=self.client,
            collection_name=f'kb_global', 
            embedding_function=self.embedding_function,
            )
        return vector_store

