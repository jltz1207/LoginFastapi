from typing import Annotated, Optional
from uuid import UUID

from langchain_core.documents import Document
from langchain_core.messages import BaseMessage
from langgraph.graph import add_messages
from pydantic import BaseModel
from app.core.config import settings

class BaseAgentState(BaseModel):
    user_id: str
    tenant_id: str
    knowledge_base_id: str
    chat_history: Annotated[list[BaseMessage], add_messages] = []
    query: str
    answer: str = ""
    resolved_query: str = ""
    documents: list[Document] = []
    model_used: str = ""
    loop_count: int = 0

    # Budget counters. Plain ints (last-write-wins) rather than operator.add
    # reducers: only `generate` writes them, and an absolute write is what lets
    # `retrieve_docs` reset them at the start of every run. With an additive
    # reducer the checkpointed totals would keep growing across turns of the
    # same thread and permanently exhaust the budget.
    tool_calls_count: int = 0
    token_count: int = 0


class LookupAgentState(BaseAgentState): # lookup subgraph
    standalone_query: str = ""
    grade: Optional[int] = None

class RoutedAgentState(BaseAgentState):
    route: str
    confidence: float
    filters: dict
    trace: list[str]