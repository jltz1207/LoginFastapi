from langchain_deepseek import ChatDeepSeek
from langchain_ollama import ChatOllama

from app.core.config import settings
from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI


class LLMFactory():
    @classmethod
    def get_model(cls, tools:list = None) -> BaseChatModel:
        primary_model = cls.create_model(settings.LLM_PROVIDER_TYPE, settings.LLM_MODEL_NAME, settings.LLM_API_KEY)
        fallback_model = cls.create_model(settings.FALLBACK_LLM_PROVIDER_TYPE, settings.FALLBACK_LLM_MODEL_NAME, settings.FALLBACK_LLM_API_KEY)
        if tools:
            primary_model.bind_tools(tools)
            fallback_model.bind_tools(tools)

        resilient_model = primary_model.with_fallbacks([fallback_model])
        return resilient_model
    
    @classmethod
    def create_model(cls, type: str, model_name: str, api_key:str = None) -> BaseChatModel:
        provider_type_upper = type.upper()
        if provider_type_upper == "GEMINI":
            return ChatGoogleGenerativeAI(
                model=model_name,
                api_key=api_key
            )
        elif provider_type_upper == "OPENAI":
            return ChatOpenAI(
                model=model_name,
                api_key=api_key
            )
        elif provider_type_upper == "DEEPSEEK":
            return ChatDeepSeek(
                model=model_name,
                api_key=api_key
            )
        elif provider_type_upper == "OLLAMA":
            return ChatOllama(
                model=model_name,      # e.g., "llama3.1"
                temperature=0.0,       # Set to 0 for strict grading/routing tasks
                # base_url="http://localhost:11434" -> Default, usually omitted
            )
        raise ValueError(f"Unsupported LLM_PROVIDER_TYPE: {settings.LLM_PROVIDER_TYPE!r}")
    


