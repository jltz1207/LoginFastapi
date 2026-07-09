from uuid import UUID
from pydantic import BaseModel


class ChatRequestModel(BaseModel):
    knowledge_base_id: UUID
    question: str

class ChatResponseModel(BaseModel):
    knowledge_base_id: UUID
    question: str
    answer: str
    source: list[str]
