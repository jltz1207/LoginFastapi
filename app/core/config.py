from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
import os

class Settings(BaseSettings):
    # Database - PostgreSQL (URL-encode special chars in password: @ = %40)
    # Required, no default: set it in .env.dev / .env.prod so credentials never live in code.
    DATABASE_URL: str

    # Required, no default: signs JWTs (HS256), so it must be secret and at least 256 bits.
    SECRET_KEY: str = Field(min_length=32)
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 120

    # ChromaDB settings
    CHROMA_PERSIST_DIRECTORY: str = "./chroma_data"
    EMBEDDING_MODEL: str = "gemini-embedding-001"
    GEMINI_API_KEY:str  =""
    COLLECTION_NAME:str = "user_knowledge_base"
    PARTITION_METHOD: int = 3  # 1 = GLOBAL_COLLECTION, 2 = ONE_USER_ONE_COLLECTION, 3 = ONE_TENANT_ONE_COLLECTION
    EMBEDDING_PROVIDE_TYPE:str ="GEMINI"
    # Part of every collection name. Bump it whenever EMBEDDING_MODEL / EMBEDDING_PROVIDE_TYPE
    # (or anything else that changes the vectors) changes, so old and new vectors never share
    # a collection. Existing data must be migrated: see scripts/migrate_collection_names.py.
    EMBEDDING_VERSION: str = "v1"

    # LLM
    LLM_PROVIDER_TYPE: str = "GEMINI"
    LLM_MODEL_NAME: str = "gemini-3.5-flash"
    LLM_API_KEY: str = ""
    LLM_TIMEOUT_SECONDS: float = 15.0
    LLM_MAX_RETRIES: int = 1

    FALLBACK_LLM_PROVIDER_TYPE: str = "DEEPSEEK"
    FALLBACK_LLM_MODEL_NAME: str = "deepseek-v4-flash"
    FALLBACK_LLM_API_KEY: str = ""
    FALLBACK_LLM_TIMEOUT_SECONDS: float = 60.0
    FALLBACK_LLM_MAX_RETRIES: int = 1

    FALLBACK_LLM_PROVIDER_TYPE_2: str = "DEEPSEEK"
    FALLBACK_LLM_MODEL_NAME_2: str = "deepseek-v4-flash"
    FALLBACK_LLM_API_KEY_2: str = ""
    FALLBACK_LLM_TIMEOUT_SECONDS_2: float = 60.0
    FALLBACK_LLM_MAX_RETRIES_2: int = 1
    
    # Logging
    LOG_LEVEL: str = "INFO"   # DEBUG | INFO | WARNING | ERROR
    LOG_FORMAT: str = "console"  # console | json
    LOCAL_STORAGE_ROOT: str = "./storages"
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    TAVILY_API_KEY: str = ""
    # Default: GraphStrategy.ROUTED (query router + six branches). Set 2 (SEARCH, LOOKUP only)
    # as a kill switch; both strategies share the same thread_id and checkpoints.
    GRAPH_STRATEGY: int = 4

    # Ingestion worker (app/ingestion/). Runs inside the API process, see app/main.py.
    INGESTION_WORKER_ENABLED: bool = True
    INGESTION_POLL_INTERVAL_SECONDS: float = 5.0  # fallback; upload wakes the worker directly
    # A PROCESSING row older than this is treated as abandoned (worker died) and requeued.
    # Must exceed the longest expected ingestion, or a live job gets claimed twice.
    INGESTION_LEASE_SECONDS: int = 1800
    INGESTION_MAX_ATTEMPTS: int = 3

    # Document summary (map-reduce over documents.raw_text)
    SUMMARY_SECTION_CHARS: int = 12_000
    SUMMARY_BATCH_SIZE: int = 8
    SUMMARY_MAX_CONCURRENCY: int = 4

    # BudgetLimits
    MAX_TOOL_CALLS: int = 3
    MAX_TOKENS: int = 20_000
    MAX_TOOL_RESULT_CHARS: int = 8_000

app_env = os.getenv("APP_ENV", "dev")

if app_env == "prod":
    settings = Settings(_env_file=".env.prod") # dockerg
else:
    settings = Settings(_env_file=".env.dev") # local