from uuid import UUID
from pydantic import BaseModel


class ChatRequestModel(BaseModel):
    knowledge_base_id: UUID
    question: str
