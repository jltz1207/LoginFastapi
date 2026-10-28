"""The compiled routed graph end to end, with every node replaced by a fake.

No LLM, Chroma or DB is touched. The checkpointer is an in-memory saver, so
multi-turn behaviour (same thread_id) is exercised for real.
"""

import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.graphs.routed_rag import RoutedGraphFactory, initial_state
from app.routing.resolver import QueryResolver
from app.routing.router.base import BaseRouter
from app.routing.taxonomy import Route, RouteDecision


class _FixedRouter(BaseRouter):
    def __init__(self, route):
        self.route_value = route

    async def route(self, query, ctx):
        return RouteDecision(route=self.route_value, confidence=0.9, accepted=True)


class _RecordingRewriter:
    def __init__(self):
        self.histories: list[list] = []

    async def rewrite(self, query, history):
        self.histories.append(list(history))
        return query


async def _lookup(state):
    # Mirrors the real LOOKUP subgraph: the answer lands in chat_history, not `answer`.
    return {"chat_history": [AIMessage(content=f"lookup answer to {state.query}")]}


def _branch(name):
    async def run(state):
        return {"answer": f"{name} answer to {state.query}", "documents": []}

    return run


_BRANCHES = {
    "global_summary": _branch("global"),
    "metadata": _branch("metadata"),
    "multi_hop": _branch("multi_hop"),
    "no_retrieval": _branch("no_retrieval"),
    "meta": _branch("meta"),
}


def _graph(route, rewriter=None, checkpointer=None):
    router = _FixedRouter(route)
    return RoutedGraphFactory.build(
        checkpointer or InMemorySaver(),
        router=router,
        fallback=router,
        resolver=QueryResolver(rewriter=rewriter or _RecordingRewriter()),
        lookup=_lookup,
        **_BRANCHES,
    )


def _input(query):
    return initial_state(
        query=query,
        knowledge_base_id="kb-1",
        tenant_id="t-1",
        user_id="u-1",
        chat_history=[HumanMessage(content=query)],
    )


_CONFIG = {"configurable": {"thread_id": "u-1::kb-1"}}


def test_second_turn_sees_the_first_turn():
    rewriter = _RecordingRewriter()
    graph = _graph(Route.LOOKUP, rewriter=rewriter)

    async def run():
        await graph.ainvoke(_input("退款政策有哪幾點？"), _CONFIG)
        await graph.ainvoke(_input("那第二點呢？"), _CONFIG)

    asyncio.run(run())

    # Turn 1 has no history, so the rewriter only runs on turn 2.
    (history,) = rewriter.histories
    assert [m.content for m in history] == ["退款政策有哪幾點？", "lookup answer to 退款政策有哪幾點？"]
