from pydantic_settings import BaseSettings, SettingsConfigDict
import os

class Settings(BaseSettings):
    # Database - PostgreSQL (URL-encode special chars in password: @ = %40)
    DATABASE_URL: str = "postgresql://postgres:P%40ssw0rd@localhost:5432/AsistAi"

    # Optional: JWT secret, etc.
    SECRET_KEY: str = "a-string-secret-at-least-256-bits-long"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # ChromaDB settings
    CHROMA_PERSIST_DIRECTORY: str = "./chroma_data"
    CHROMA_COLLECTION_NAME: str = "users_knowledge"

    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    OPENAI_API_KEY: str = ""
  
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

app_env = os.getenv("APP_ENV", "dev")

if app_env == "prod":
    settings = Settings(_env_file=".env.prod") # docker
else:
    settings = Settings(_env_file=".env.dev") # local