from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Database - PostgreSQL (URL-encode special chars in password: @ = %40)
    DATABASE_URL: str = "postgresql://postgres:P%40ssw0rd@localhost:5432/AsistAi"

    # Optional: JWT secret, etc.
    SECRET_KEY: str = "a-string-secret-at-least-256-bits-long"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
  
    class Config:
        env_file = ".env"

settings = Settings()