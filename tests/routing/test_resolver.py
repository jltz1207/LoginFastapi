"""Query resolver wiring (`ResolverNode`) and `QueryResolver` degradation.

No LLM is touched: the rewriter is a fake that records what it was given.
"""

import asyncio

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent.graphs.routed_rag import ResolverNode, RouterNode
from app.agent.state import RoutedAgentState
from app.routing.resolver import QueryResolver
from app.routing.router.base import BaseRouter
from app.routing.taxonomy import Route, RouteDecision


class _RecordingRewriter:
    def __init__(self, result="rewritten"):
        self.calls: list[tuple[str, list]] = []
        self._result = result

    async def rewrite(self, query, history):
        self.calls.append((query, list(history)))
        return self._result


class _RecordingRouter(BaseRouter):
    def __init__(self):
        self.contexts = []

    async def route(self, query, ctx):
        self.contexts.append(ctx)
        return RouteDecision(route=Route.LOOKUP, confidence=1.0, accepted=True)


def _state(chat_history, query="那第二點呢？"):
    return RoutedAgentState(
        query=query,
        tenant_id="t-1",
        user_id="u-1",
        knowledge_base_id="kb-1",
        chat_history=chat_history,
    )


def test_first_turn_does_not_call_rewriter():
    rewriter = _RecordingRewriter()
    node = ResolverNode(resolver=QueryResolver(rewriter=rewriter))

    update = asyncio.run(node(_state([HumanMessage("你好嗎？")], query="你好嗎？")))

    assert rewriter.calls == []
    assert update["resolved_query"] == "你好嗎？"


def test_rewriter_gets_prior_turns_without_current_question():
    rewriter = _RecordingRewriter()
    node = ResolverNode(resolver=QueryResolver(rewriter=rewriter))
    history = [
        HumanMessage("退款政策有哪幾點？"),
        AIMessage("有三點……"),
        ToolMessage("web search result", tool_call_id="x"),
        HumanMessage("那第二點呢？"),
    ]

    asyncio.run(node(_state(history)))

    (query, passed_history), = rewriter.calls
    assert query == "那第二點呢？"
    assert [m.content for m in passed_history] == ["退款政策有哪幾點？", "有三點……"]


def test_router_context_excludes_current_question():
    router = _RecordingRouter()
    node = RouterNode(router=router, fallback=router)
    history = [HumanMessage("退款政策有哪幾點？"), AIMessage("有三點……"), HumanMessage("那第二點呢？")]

    asyncio.run(node(_state(history)))

    assert router.contexts[0].resolved_history == ["退款政策有哪幾點？", "有三點……"]


class _FailingRewriter:
    async def rewrite(self, query, history):
        raise RuntimeError("quota exceeded")


def test_rewriter_failure_degrades_to_original_query():
    node = ResolverNode(resolver=QueryResolver(rewriter=_FailingRewriter()))
    history = [HumanMessage("退款政策有哪幾點？"), AIMessage("有三點……"), HumanMessage("那第二點呢？")]

    update = asyncio.run(node(_state(history)))

    assert update["resolved_query"] == "那第二點呢？"
    assert update["trace"] == ["resolve: rewriter failed (RuntimeError), using original query"]


class _CancelledRewriter:
    async def rewrite(self, query, history):
        raise asyncio.CancelledError


def test_rewriter_cancellation_propagates():
    node = ResolverNode(resolver=QueryResolver(rewriter=_CancelledRewriter()))
    history = [HumanMessage("a"), AIMessage("b"), HumanMessage("c")]

    try:
        asyncio.run(node(_state(history)))
    except asyncio.CancelledError:
        return
    raise AssertionError("CancelledError was swallowed")


def test_llm_rewriter_uses_llm_factory(monkeypatch):
    from langchain_core.messages import AIMessage as _AI

    from app.llm.factory import LLMFactory
    from app.routing.resolver import LLMQueryRewriter

    class _FakeModel:
        def __init__(self):
            self.messages = None

        async def ainvoke(self, messages):
            self.messages = messages
            return _AI("退款政策的第二點是什麼？")

    fake = _FakeModel()
    monkeypatch.setattr(LLMFactory, "get_model", classmethod(lambda cls, tools=None: fake))

    result = asyncio.run(LLMQueryRewriter().rewrite("那第二點呢？", [HumanMessage("退款政策有哪幾點？")]))

    assert result == "退款政策的第二點是什麼？"
    assert fake.messages is not None


def test_history_is_capped_to_recent_turns():
    rewriter = _RecordingRewriter()
    node = ResolverNode(resolver=QueryResolver(rewriter=rewriter))
    history = []
    for i in range(10):
        history += [HumanMessage(f"q{i}"), AIMessage(f"a{i}")]
    history.append(HumanMessage("那第二點呢？"))

    asyncio.run(node(_state(history)))

    (_, passed_history), = rewriter.calls
    assert [m.content for m in passed_history] == ["q7", "a7", "q8", "a8", "q9", "a9"]
