from functools import lru_cache
import chromadb
from app.core import settings

class Chroma_db:
    def __init__(self) -> None:
        self.client = chromadb.PersistentClient(path=settings.CHROMA_PERSIST_DIRECTORY)

@lru_cache
def get_chroma_db() -> Chroma_db:
    return Chroma_db()
