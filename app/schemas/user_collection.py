from pydantic import BaseModel
from datetime import datetime


class UserCollectionResponse(BaseModel):
    id: int
    user_id: int
    collection_name: str
    document_count: int
    created_at: datetime
    updated_at: datetime | None = None

    class Config:
        from_attributes = True
