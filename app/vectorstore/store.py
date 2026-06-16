from anyio.functools import lru_cache
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.user_collection import UserCollection
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings
from app.core import settings
from langchain_core.documents import Document
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from app.vectorstore import collection_manager

class store_indexer:
    def __init__(self):
        self.collections = collection_manager()
    
    def add_documents(self, docs: list[Document],  user_id: str, metadata: dict = {}):
        user_collection = self.collections.get_or_create_collection(user_id)
        if user_collection:
            user_collection.add_documents([Document(page_content = doc.page_content, metadata= metadata | doc.metadata) for doc in docs])

@lru_cache(maxsize=1)
def get_vector_store_indexer() -> store_indexer:
    return store_indexer()