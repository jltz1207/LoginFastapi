from langchain_core.messages import HumanMessage
from starlette.exceptions import HTTPException

from app.agent.graphs.base import GraphStrategy
from app.agent.graphs.lookup_rag import LookupRagGraphFactory
from app.agent.graphs.routed_rag import RoutedGraphFactory, initial_state
from app.agent.persistence.client import get_checkpointer
from app.agent.state import BaseAgentState, LookupAgentState
from app.core.config import settings

_STRATEGY_FACTORIES = {
    GraphStrategy.SEARCH: LookupRagGraphFactory,
    GraphStrategy.ROUTED: RoutedGraphFactory
}


def _resolve_strategy() -> GraphStrategy:
    strategy_value = settings.GRAPH_STRATEGY
    try:
        strategy = GraphStrategy(strategy_value)
    except ValueError:
        raise HTTPException(
            status_code=500,
            detail=f"Unknown graph strategy: {strategy_value}"
        )
    if strategy not in _STRATEGY_FACTORIES:
        raise HTTPException(
            status_code=500,
            detail=f"Graph strategy '{strategy.name}' is not yet implemented"
        )
    return strategy


async def get_compiled_graph():
    factory = _STRATEGY_FACTORIES[_resolve_strategy()]
    checkpointer = get_checkpointer()
    compiled_graph = factory.build(checkpointer=checkpointer)
    return compiled_graph


def build_initial_state(*, user_id: str, tenant_id: str, knowledge_base_id: str, question: str) -> BaseAgentState:
    """The input state for one chat turn, in the schema of the graph `get_compiled_graph()` builds.

    This turn's question goes into `chat_history` as a `HumanMessage`; the checkpointer
    appends it to the thread's earlier turns (`add_messages` reducer).
    """
    chat_history = [HumanMessage(content=question)]
    if _resolve_strategy() is GraphStrategy.ROUTED:
        return initial_state(
            query=question,
            knowledge_base_id=knowledge_base_id,
            tenant_id=tenant_id,
            user_id=user_id,
            chat_history=chat_history,
        )
    return LookupAgentState(
        user_id=user_id,
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        query=question,
        chat_history=chat_history,
    )
