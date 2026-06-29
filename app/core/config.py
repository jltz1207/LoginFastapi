from pydantic_settings import BaseSettings, SettingsConfigDict
import os

class Settings(BaseSettings):
    # Database - PostgreSQL (URL-encode special chars in password: @ = %40)
    DATABASE_URL: str = "postgresql+psycopg://postgres:password@localhost:5432/AsistAi"

    # Optional: JWT secret, etc.
    SECRET_KEY: str = "a-string-secret-at-least-256-bits-long"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 120

    # ChromaDB settings
    CHROMA_PERSIST_DIRECTORY: str = "./chroma_data"
    EMBEDDING_MODEL: str = "gemini-embedding-001"
    GEMINI_API_KEY:str  =""
    COLLECTION_NAME:str = "user_knowledge_base"
    PARTITION_METHOD: int = 2  # 1 = GLOBAL_COLLECTION, 2 = ONE_USER_ONE_COLLECTION
    EMBEDDING_PROVIDE_TYPE:str ="GEMINI"

    # LLM 
    LLM_PROVIDER_TYPE: str = "GEMINI"
    LLM_MODEL_NAME: str = "gemini-3.5-flash"
    LLM_API_KEY: str = ""

    # Logging
    LOG_LEVEL: str = "INFO"   # DEBUG | INFO | WARNING | ERROR
    LOG_FORMAT: str = "console"  # console | json
    LOCAL_STORAGE_ROOT: str = "./storages"
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    TAVILY_API_KEY: str = ""
    GRAPH_STRATEGY: int = 2  # Default: GraphStrategy.SEARCH
app_env = os.getenv("APP_ENV", "dev")

if app_env == "prod":
    settings = Settings(_env_file=".env.prod") # dockerg
else:
    settings = Settings(_env_file=".env.dev") # local