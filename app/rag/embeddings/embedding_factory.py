
from langchain_google_genai import GoogleGenerativeAIEmbeddings

from app.core import settings


class EmbeddingFactory:
    provide_type = settings.EMBEDDING_PROVIDE_TYPE

    @classmethod
    def get_embedding_function(cls):
        if settings.EMBEDDING_MODEL:
            return None
        if cls.provide_type.lower() == 'gemini':
            gemini_ef = GoogleGenerativeAIEmbeddings(
            model = settings.EMBEDDING_MODEL,
            google_api_key=settings.GEMINI_API_KEY
            )
            return gemini_ef
        elif cls.provide_type == 'local':
            print("handle local later")
        else:
            raise ValueError(f"Unsupported embedding provider: '{cls.provide_type}'")