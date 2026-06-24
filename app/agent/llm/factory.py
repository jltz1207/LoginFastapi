from app.core.config import settings
from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI


class LLMFactory():
    @staticmethod
    def get_model() -> BaseChatModel:
        if settings.LLM_PROVIDER_TYPE == "GEMINI":
            return ChatGoogleGenerativeAI(
                model=settings.LLM_MODEL_NAME,
                api_key=settings.LLM_API_KEY
            )
        elif settings.LLM_PROVIDER_TYPE == "OPENAI":
            return ChatOpenAI(
                model=settings.LLM_MODEL_NAME,
                api_key=settings.LLM_API_KEY
            )
        raise ValueError(f"Unsupported LLM_PROVIDER_TYPE: {settings.LLM_PROVIDER_TYPE!r}")