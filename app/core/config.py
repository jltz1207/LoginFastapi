from pydantic_settings import BaseSettings

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
    CHROMA_OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    OPENAI_API_KEY: str = ""
  
    class Config:
        env_file = ".env"

settings = Settings()