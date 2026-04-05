from fastapi import FastAPI
from app.api.v1.api import api_router
from app.db.session import engine
from app.models import Base  # Import to register models

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

app = FastAPI(openapi_tags=tags_metadata)

app.include_router(api_router, prefix="/api/v1") # base url = server + prefix