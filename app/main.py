import os
from dotenv import load_dotenv

_app_env = os.getenv("APP_ENV", "dev")
load_dotenv(f".env.{_app_env}")  # must run before LangChain imports so LANGCHAIN_* vars reach os.environ

from app.core.logging import setup_logging  # must be first — configures root logger

setup_logging()

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.security import HTTPBearer
from app.api.v1.api import api_router
from app.db.session import engine
from app.middleware.request_logger import RequestLoggerMiddleware
from app.models import Base  # Import to register models
from app.agent.persistance.client import checkpointer_lifespan


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with checkpointer_lifespan():
        yield

tags_metadata = [
    {
        "name": "Users",
        "description": "Operations with **Users**. The *login* logic is also handled here.",
    },
    {
        "name": "AI",
        "description": "Powerful AI processing endpoints.",
        "externalDocs": {
            "description": "Learn more about our AI Model",
            "url": "https://openai.com",
        },
    },
]

app = FastAPI(openapi_tags=tags_metadata, lifespan=lifespan)
app.add_middleware(RequestLoggerMiddleware)

security_scheme = HTTPBearer()
app.include_router(api_router, prefix="/api/v1") # base url = server + prefix