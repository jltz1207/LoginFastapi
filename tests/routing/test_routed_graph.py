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


def test_thread_previously_run_by_the_lookup_graph_is_readable():
    """GRAPH_STRATEGY 2 → 4 keeps the same thread_id; the old checkpoint has LookupAgentState channels."""
    from langgraph.graph import END, START, StateGraph

    from app.agent.state import LookupAgentState

    async def old_generate(state):
        return {"chat_history": [AIMessage("old answer")], "standalone_query": "x", "grade": 9}

    old = StateGraph(LookupAgentState)
    old.add_node("generate", old_generate)
    old.add_edge(START, "generate")
    old.add_edge("generate", END)

    saver = InMemorySaver()
    rewriter = _RecordingRewriter()
    old_input = LookupAgentState(
        query="舊問題", tenant_id="t-1", user_id="u-1", knowledge_base_id="kb-1",
        chat_history=[HumanMessage("舊問題")],
    )

    async def run():
        await old.compile(checkpointer=saver).ainvoke(old_input, _CONFIG)
        return await _graph(Route.GLOBAL, rewriter=rewriter, checkpointer=saver).ainvoke(_input("新問題"), _CONFIG)

    result = asyncio.run(run())

    assert result["answer"] == "global answer to 新問題"
    (history,) = rewriter.histories
    assert [m.content for m in history] == ["舊問題", "old answer"]


def test_thread_previously_run_by_the_routed_graph_is_readable_by_lookup():
    """Kill switch: GRAPH_STRATEGY 4 → 2 on a thread that has RoutedAgentState channels."""
    from langgraph.graph import END, START, StateGraph

    from app.agent.state import LookupAgentState

    seen: list[list[str]] = []

    async def old_generate(state):
        seen.append([m.content for m in state.chat_history])
        return {"chat_history": [AIMessage("lookup answer")]}

    lookup_only = StateGraph(LookupAgentState)
    lookup_only.add_node("generate", old_generate)
    lookup_only.add_edge(START, "generate")
    lookup_only.add_edge("generate", END)

    saver = InMemorySaver()
    lookup_input = LookupAgentState(
        query="第二題", tenant_id="t-1", user_id="u-1", knowledge_base_id="kb-1",
        chat_history=[HumanMessage("第二題")],
    )

    async def run():
        await _graph(Route.GLOBAL, checkpointer=saver).ainvoke(_input("第一題"), _CONFIG)
        await lookup_only.compile(checkpointer=saver).ainvoke(lookup_input, _CONFIG)

    asyncio.run(run())

    assert seen == [["第一題", "global answer to 第一題", "第二題"]]


def test_final_answer_tag_reaches_stream_events():
    """The SSE contract in `chats.py`: a branch's tagged ainvoke() streams token events carrying the tag."""
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    from app.routing.branches.no_retrieval import LLMDirectAnswerLLM, NoRetrievalBranch

    model = GenericFakeChatModel(messages=iter([AIMessage("巴黎 是 首都")]))
    router = _FixedRouter(Route.NO_RETRIEVAL)
    graph = RoutedGraphFactory.build(
        InMemorySaver(),
        router=router,
        fallback=router,
        resolver=QueryResolver(rewriter=_RecordingRewriter()),
        lookup=_lookup,
        **{**_BRANCHES, "no_retrieval": NoRetrievalBranch(llm=LLMDirectAnswerLLM(model=model))},
    )

    async def run():
        return [e async for e in graph.astream_events(_input("法國首都？"), _CONFIG, version="v2")]

    events = asyncio.run(run())

    streamed = [e for e in events if e["event"] == "on_chat_model_stream"]
    assert len(streamed) > 1  # token by token, not one final chunk
    assert all("final_answer" in e["tags"] for e in streamed)
    assert {e["metadata"]["langgraph_node"] for e in streamed} == {"no_retrieval"}
    assert "".join(e["data"]["chunk"].text for e in streamed) == "巴黎 是 首都"
    (respond_end,) = [e for e in events if e["event"] == "on_chain_end" and e["name"] == "respond"]
    assert respond_end["data"]["output"]["answer"] == "巴黎 是 首都"


@pytest.mark.parametrize(
    "route,expected",
    [
        (Route.LOOKUP, "lookup answer to q"),
        (Route.GLOBAL, "global answer to q"),
        (Route.METADATA, "metadata answer to q"),
        (Route.MULTI_HOP, "multi_hop answer to q"),
        (Route.NO_RETRIEVAL, "no_retrieval answer to q"),
        (Route.META, "meta answer to q"),
    ],
)
def test_every_route_answers_in_answer_and_chat_history(route, expected):
    result = asyncio.run(_graph(route).ainvoke(_input("q"), _CONFIG))

    assert result["route"] == route.value
    assert result["answer"] == expected
    assert [(m.type, m.content) for m in result["chat_history"]] == [("human", "q"), ("ai", expected)]


def test_non_lookup_answer_reaches_the_next_turns_resolver():
    rewriter = _RecordingRewriter()
    graph = _graph(Route.GLOBAL, rewriter=rewriter)

    async def run():
        await graph.ainvoke(_input("這些文件在講什麼？"), _CONFIG)
        await graph.ainvoke(_input("再詳細一點"), _CONFIG)

    asyncio.run(run())

    (history,) = rewriter.histories
    assert [m.content for m in history] == ["這些文件在講什麼？", "global answer to 這些文件在講什麼？"]
