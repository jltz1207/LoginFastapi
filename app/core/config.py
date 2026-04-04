from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Database - defaults to SQLite for development
    DATABASE_URL: str = "sqlite:///./test.db"  # Change to PostgreSQL for production

    # Optional: JWT secret, etc.
    SECRET_KEY: str = "a-string-secret-at-least-256-bits-long"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
  
    class Config:
        env_file = ".env"

settings = Settings()