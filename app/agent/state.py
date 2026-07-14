from typing import Annotated, Optional
from uuid import UUID

from langchain_core.documents import Document
from langchain_core.messages import BaseMessage
from langgraph.graph import add_messages
from pydantic import BaseModel



class AgentState(BaseModel):
    user_id: UUID
    knoweledge_base_id: UUID
    question: str
    chat_messages: Annotated[list[BaseMessage], add_messages]
    standalone_question: str = ""
    documents: list[Document] = []
    loop_count: int = 0
    grade: Optional[int] = None
    model_used: str = ""
