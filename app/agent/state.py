from typing import Annotated, Any, Optional
from uuid import UUID

from langchain_core.documents import Document
from langchain_core.messages import BaseMessage
from langgraph.graph import add_messages
from pydantic import BaseModel, Field
from app.core.config import settings

class Chunk(BaseModel):
    """一段可被引用的內容：檢索出的段落、文件摘要、或多跳查詢的中間結果。"""

    chunk_id: str | None
    content: str
    source: str = ""
    score: float = 0.0
    metadata: dict = Field(default_factory=dict)

class BaseAgentState(BaseModel):
    user_id: str
    tenant_id: str
    knowledge_base_id: str
    chat_history: Annotated[list[BaseMessage], add_messages] = []
    query: str
    answer: str = ""
    resolved_query: str = ""
    documents: list[Chunk] = []
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
    # Widened from the inherited `list[Document]`. The leaf branches write
    # heterogeneous payloads: GLOBAL and MULTI_HOP emit
    # `routing.branches.common.Chunk`, METADATA emits raw SQL rows (dicts).
    # A TypedDict state never validated this; a Pydantic one does, so the field
    # is widened here instead of forcing every branch through langchain Document.

    # ResolverNode/RouterNode read the inherited `chat_history`
    # (list[BaseMessage] + add_messages reducer); there is no separate
    # conversation history on this state.

    # All defaulted so a caller can build a state from just the query fields;
    # `route`/`confidence` stay empty until RouterNode writes them, and
    # `confidence=0.0` falls below CONFIDENCE_THRESHOLD so select_branch's
    # LOOKUP fallback covers the unrouted case.
    route: str = ""
    confidence: float = 0.0
    filters: dict = {}
    trace: list[str] = []
    sql_documents: list[any] = []