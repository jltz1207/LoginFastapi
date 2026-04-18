from functools import lru_cache
from typing import Any

import chromadb
from chromadb.utils import embedding_functions

from app.core import settings


class ChromaService:
    def __init__(self) -> None:
        self.client = chromadb.PersistentClient(path=settings.CHROMA_PERSIST_DIRECTORY)
        self.collection = self.client.get_or_create_collection(
            name=settings.CHROMA_COLLECTION_NAME,
            embedding_function=self._build_embedding_function(),
        )

    def _build_embedding_function(self):
        if settings.OPENAI_API_KEY:
            return embedding_functions.OpenAIEmbeddingFunction(
                api_key=settings.OPENAI_API_KEY,
                model_name=settings.CHROMA_OPENAI_EMBEDDING_MODEL,
            )
        return None

    def upsert(
        self,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict[str, Any]] | None = None,
    ) -> None:
        self.collection.upsert(ids=ids, documents=documents, metadatas=metadatas)

    def query(
        self,
        query_text: str,
        n_results: int = 5,
        where: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.collection.query(
            query_texts=[query_text],
            n_results=n_results,
            where=where,
        )


@lru_cache
def get_chroma_service() -> ChromaService:
    return ChromaService()
