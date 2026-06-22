
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel
from pypika import Field

# model create form
class KnowledgeBaseCreateRequest(BaseModel):
    # user_id, handle by session
    title:Optional[str] 

class KnowledgeBaseCreateResponse(BaseModel):
    id: UUID # Automatically validates that the client sent a real UUID string
    user_id:UUID
    title:str
    status:str #enum to str
    last_message_at: Optional[datetime]
    created_dt: Optional[datetime]
    modified_dt: Optional[datetime]
    deleted_at: Optional[datetime]
    class Config:
        from_attributes = True  # Allows conversion from SQLAlchemy models
    
class QueryAsistantRequest(BaseModel):
    question: str
    knoweldge_base_id: UUID
class QueryAsistantResponse(BaseModel):
    question: str
    knoweldge_base_id: UUID
    message_id: UUID