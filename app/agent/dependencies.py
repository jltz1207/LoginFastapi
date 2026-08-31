from app.agent.graphs.routed_rag import RoutedGraphFactory
from starlette.exceptions import HTTPException
from app.agent.graphs.base import GraphStrategy
from app.agent.graphs.lookup_rag import LookupRagGraphFactory
from app.agent.persistence.client import get_checkpointer
from app.core.config import settings

_STRATEGY_FACTORIES = {
    GraphStrategy.SEARCH: LookupRagGraphFactory,
    GraphStrategy.ROUTED: RoutedGraphFactory
}

async def get_compiled_graph():
    strategy_value = settings.GRAPH_STRATEGY
    try:
        strategy = GraphStrategy(strategy_value)
    except ValueError:
        raise HTTPException(
            status_code=500,
            detail=f"Unknown graph strategy: {strategy_value}"
        )
    factory = _STRATEGY_FACTORIES.get(strategy)
    if factory is None:
        raise HTTPException(
            status_code=500,
            detail=f"Graph strategy '{strategy.name}' is not yet implemented"
        )
    checkpointer = get_checkpointer()
    compiled_graph = factory.build(checkpointer=checkpointer)
    return compiled_graph