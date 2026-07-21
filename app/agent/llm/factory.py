import asyncio
import functools
from typing import Optional, Tuple

from langchain_deepseek import ChatDeepSeek
from langchain_groq import ChatGroq
from langchain_ollama import ChatOllama

from app.core.config import settings
from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI

from google.genai.errors import ServerError, ClientError
from httpx import TimeoutException, ConnectError


class LLMFactory():
    _resilient_model_cache: dict = {}

    @classmethod
    def get_model(cls, tools: list = None) -> BaseChatModel:
        tool_names = tuple(getattr(tool, "name", str(tool)) for tool in tools) if tools else ()
        cached = cls._resilient_model_cache.get(tool_names)
        if cached is not None:
            return cached

        primary_model, fallback_model, fallback_model_2 = cls._get_base_models()
        if tools:
            primary_model = primary_model.bind_tools(tools)
            fallback_model = fallback_model.bind_tools(tools)
            fallback_model_2 = fallback_model_2.bind_tools(tools)


        resilient_model = primary_model.with_fallbacks(
                fallbacks=[fallback_model, fallback_model_2] # without exceptions_to_handle, catch all standard model exception
            )
        cls._resilient_model_cache[tool_names] = resilient_model
        return resilient_model

    # [ First Call ]  ──► Checks Cache (Miss!) ──► Runs create_model() ──► Saves to Cache ──► Returns Models
    # [ Next Calls ]  ──► Checks Cache (Hit!)  ───────────────────────────────────────────► Returns Models
    @staticmethod
    @functools.lru_cache(maxsize=1)
    def _get_base_models() -> Tuple[BaseChatModel, BaseChatModel]:
        primary_model = LLMFactory.create_model(
            settings.LLM_PROVIDER_TYPE,
            settings.LLM_MODEL_NAME,
            settings.LLM_API_KEY,
            timeout=settings.LLM_TIMEOUT_SECONDS,
            max_retries=settings.LLM_MAX_RETRIES,
        )
        fallback_model = LLMFactory.create_model(
            settings.FALLBACK_LLM_PROVIDER_TYPE,
            settings.FALLBACK_LLM_MODEL_NAME,
            settings.FALLBACK_LLM_API_KEY,
            timeout=settings.FALLBACK_LLM_TIMEOUT_SECONDS,
            max_retries=settings.FALLBACK_LLM_MAX_RETRIES,
        )
        fallback_model_2 = LLMFactory.create_model(
            settings.FALLBACK_LLM_PROVIDER_TYPE,
            settings.FALLBACK_LLM_MODEL_NAME,
            settings.FALLBACK_LLM_API_KEY,
            timeout=settings.FALLBACK_LLM_TIMEOUT_SECONDS,
            max_retries=settings.FALLBACK_LLM_MAX_RETRIES,
        )
        return primary_model, fallback_model, fallback_model_2

    @classmethod
    def create_model(
        cls,
        type: str,
        model_name: str,
        api_key: str = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> BaseChatModel:
        provider_type_upper = type.upper()
        if provider_type_upper == "GEMINI":
            return ChatGoogleGenerativeAI(
                model=model_name,
                api_key=api_key,
                timeout=timeout,
                max_retries=max_retries,
            )
        elif provider_type_upper == "OPENAI":
            return ChatOpenAI(
                model=model_name,
                api_key=api_key,
                timeout=timeout,
                max_retries=max_retries,
            )
        elif provider_type_upper == "DEEPSEEK":
            return ChatDeepSeek(
                model=model_name,
                api_key=api_key,
                timeout=timeout,
                max_retries=max_retries,
            )
        elif provider_type_upper == "OLLAMA":
            return ChatOllama(
                model=model_name,      # e.g., "llama3.1"
                temperature=0.0,       # Set to 0 for strict grading/routing tasks
                client_kwargs={"timeout": timeout} if timeout else {},
                # base_url="http://localhost:11434" -> Default, usually omitted
            )
        elif provider_type_upper == "GROQ":
            return ChatGroq(
                model=model_name,
                api_key=api_key,
                timeout=timeout,
                max_retries=max_retries,
                streaming=True
            )

        raise ValueError(f"Unsupported LLM_PROVIDER_TYPE: {settings.LLM_PROVIDER_TYPE!r}")
