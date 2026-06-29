from starlette.exceptions import HTTPException
from app.agent.graphs.base import GraphStrategy
from app.agent.graphs.searching_rag import SearchingRagGraphFactory
from app.agent.persistance.client import get_graph_checkpointer
from app.core.config import settings

_STRATEGY_FACTORIES = {
    GraphStrategy.SEARCH: SearchingRagGraphFactory,
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
    async with get_graph_checkpointer() as checkpointer:
        compiled_graph = factory.build(checkpointer=checkpointer)
        yield compiled_graph