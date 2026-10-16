"""Cascade / fallback chain: an LLM decision must survive when the embedding layer is unsure.

The LLM layer's confidence is borrowed from embedding similarity, so on every query that
reaches it the confidence is already below CONFIDENCE_THRESHOLD. Gating on confidence
discarded every LLM decision; acceptance is now decided by each layer (`accepted`).

No Gemini or embeddings API is touched: embeddings and the structured LLM are fakes.
"""

import asyncio
import random

import pytest

from app.agent.graphs.routed_rag import RouterNode, select_branch
from app.agent.state import RoutedAgentState
from app.routing.router.base import BaseRouter, NoOpRouter
from app.routing.router.cascade import CascadeRouter
from app.routing.router.embedding import EmbeddingRouter
from app.routing.router.fallback_chain import FallbackChainRouter
from app.routing.router.llm import LLMRouter, _LLMRouteOutput
from app.routing.router.rule import RuleRouter
from app.routing.router.speculative import SpeculativeExecutor
from app.routing.taxonomy import CONFIDENCE_THRESHOLD, DEFAULT_ROUTE, Route, RouteDecision, RouterContext

_CTX = RouterContext(tenant_id="t-1", knowledge_base_id="kb-1", resolved_history=[])

_NON_DEFAULT_ROUTES = [r for r in Route if r is not DEFAULT_ROUTE]


class _FakeEmbeddings:
    """Deterministic: the same text always maps to the same 8-dim Gaussian vector."""

    def __init__(self):
        self._cache: dict[str, list[float]] = {}

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            if text not in self._cache:
                rnd = random.Random(text)
                self._cache[text] = [rnd.gauss(0, 1) for _ in range(8)]
            out.append(self._cache[text])
        return out


class _FakeStructuredLLM:
    def __init__(self, route: Route):
        self.route = route
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        return _LLMRouteOutput(route=self.route, reasoning="fake")


class _Fixed(BaseRouter):
    def __init__(self, decision: RouteDecision):
        self.decision = decision
        self.calls = 0

    async def route(self, query, ctx):
        self.calls += 1
        return self.decision


class _Raises(BaseRouter):
    def __init__(self):
        self.calls = 0

    async def route(self, query, ctx):
        self.calls += 1
        raise RuntimeError("boom")


class _BrokenEmbeddings:
    """Simulates an embeddings API outage."""

    def __init__(self):
        self.calls = 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        raise RuntimeError("embeddings API down")


def _unsure_query(emb: _FakeEmbeddings) -> str:
    """First query whose best embedding similarity is below the threshold and not a rule hit."""
    for i in range(100):
        query = f"query {i}"
        decision = asyncio.run(EmbeddingRouter(embeddings=emb).route(query, _CTX))
        if decision.confidence < CONFIDENCE_THRESHOLD:
            return query
    raise AssertionError("no query reached the LLM layer")


def _llm(route: Route, emb: _FakeEmbeddings) -> LLMRouter:
    return LLMRouter(structured_model=_FakeStructuredLLM(route), confidence_scorer=EmbeddingRouter(embeddings=emb))


@pytest.mark.parametrize("route", _NON_DEFAULT_ROUTES)
def test_cascade_adopts_llm_route_when_embedding_is_unsure(route):
    emb = _FakeEmbeddings()
    query = _unsure_query(emb)
    cascade = CascadeRouter(rule=RuleRouter(), embedding=EmbeddingRouter(embeddings=emb), llm=_llm(route, emb))

    decision = asyncio.run(cascade.route(query, _CTX))

    assert decision.route is route
    assert decision.accepted
    # confidence is still the embedding score, kept for observability only.
    assert decision.confidence < CONFIDENCE_THRESHOLD


@pytest.mark.parametrize("route", _NON_DEFAULT_ROUTES)
def test_fallback_chain_adopts_llm_route_when_embedding_is_unsure(route):
    emb = _FakeEmbeddings()
    query = _unsure_query(emb)
    chain = FallbackChainRouter(llm=_llm(route, emb), embedding=EmbeddingRouter(embeddings=emb), rule=RuleRouter())

    decision = asyncio.run(chain.route(query, _CTX))

    assert decision.route is route
    assert decision.accepted


def test_llm_router_survives_confidence_scoring_failure():
    broken = _BrokenEmbeddings()
    llm = LLMRouter(
        structured_model=_FakeStructuredLLM(Route.GLOBAL),
        confidence_scorer=EmbeddingRouter(embeddings=broken),
    )

    decision = asyncio.run(llm.route("q", _CTX))

    assert broken.calls == 1  # scoring was attempted
    assert decision.route is Route.GLOBAL
    assert decision.accepted
    assert decision.confidence == 0.0


def test_fallback_chain_uses_llm_when_embeddings_api_is_down():
    # Before P1-1 the LLM layer failed with the embeddings API, so the chain fell through to rule.
    broken = _BrokenEmbeddings()
    rule = _Fixed(RouteDecision(route=Route.META, confidence=0.95, accepted=True))
    chain = FallbackChainRouter(
        llm=LLMRouter(structured_model=_FakeStructuredLLM(Route.MULTI_HOP), confidence_scorer=EmbeddingRouter(embeddings=broken)),
        embedding=EmbeddingRouter(embeddings=broken),
        rule=rule,
    )

    decision = asyncio.run(chain.route("q", _CTX))

    assert decision.route is Route.MULTI_HOP
    assert rule.calls == 0
    assert chain.last_degradations == []


def test_llm_router_still_raises_when_llm_call_fails():
    class _BrokenLLM:
        async def ainvoke(self, messages):
            raise RuntimeError("gemini down")

    llm = LLMRouter(structured_model=_BrokenLLM(), confidence_scorer=EmbeddingRouter(embeddings=_FakeEmbeddings()))

    with pytest.raises(RuntimeError, match="gemini down"):
        asyncio.run(llm.route("q", _CTX))


def test_embedding_router_accepts_only_at_or_above_threshold():
    emb = _FakeEmbeddings()
    router = EmbeddingRouter(embeddings=emb)
    for i in range(50):
        decision = asyncio.run(router.route(f"query {i}", _CTX))
        assert decision.accepted is (decision.confidence >= CONFIDENCE_THRESHOLD)


def test_rule_router_accepts_only_on_match():
    rule = RuleRouter()
    assert asyncio.run(rule.route("你是誰？", _CTX)).accepted
    assert asyncio.run(rule.route("這份文件的作者是誰？", _CTX)).accepted
    assert not asyncio.run(rule.route("總結所有文件", _CTX)).accepted


def test_cascade_short_circuits_on_first_accepted_layer():
    rule = _Fixed(RouteDecision(route=Route.META, confidence=0.95, accepted=True))
    embedding = _Fixed(RouteDecision(route=Route.GLOBAL, confidence=0.9, accepted=True))
    llm = _Raises()

    decision = asyncio.run(CascadeRouter(rule=rule, embedding=embedding, llm=llm).route("q", _CTX))

    assert decision.route is Route.META
    assert (embedding.calls, llm.calls) == (0, 0)


def test_cascade_falls_back_to_lookup_when_no_layer_accepts():
    unsure = RouteDecision(route=Route.GLOBAL, confidence=0.4, accepted=False)
    cascade = CascadeRouter(rule=_Fixed(unsure), embedding=_Fixed(unsure), llm=_Fixed(unsure))

    decision = asyncio.run(cascade.route("q", _CTX))

    assert decision.route is DEFAULT_ROUTE
    assert decision.accepted
    assert decision.confidence == 0.4


def test_cascade_still_raises_when_llm_layer_fails():
    # Call failures stay with RouterNode -> fallback chain (P1-2 has not changed this).
    unsure = RouteDecision(route=Route.GLOBAL, confidence=0.4, accepted=False)
    cascade = CascadeRouter(rule=_Fixed(unsure), embedding=_Fixed(unsure), llm=_Raises())

    with pytest.raises(RuntimeError):
        asyncio.run(cascade.route("q", _CTX))


def test_fallback_chain_drops_unaccepted_non_llm_result_to_lookup():
    embedding = _Fixed(RouteDecision(route=Route.GLOBAL, confidence=0.5, accepted=False))
    rule = _Fixed(RouteDecision(route=Route.META, confidence=0.95, accepted=True))
    chain = FallbackChainRouter(llm=_Raises(), embedding=embedding, rule=rule)

    decision = asyncio.run(chain.route("q", _CTX))

    assert decision.route is DEFAULT_ROUTE
    assert decision.accepted
    assert rule.calls == 0  # the first layer that answered decides; rule is not consulted
    assert any("not accepted" in entry for entry in chain.last_degradations)


def test_fallback_chain_keeps_accepted_embedding_result():
    embedding = _Fixed(RouteDecision(route=Route.GLOBAL, confidence=0.9, accepted=True))
    chain = FallbackChainRouter(llm=_Raises(), embedding=embedding, rule=_Raises())

    decision = asyncio.run(chain.route("q", _CTX))

    assert decision.route is Route.GLOBAL


def _state(**kwargs) -> RoutedAgentState:
    base = dict(query="q", resolved_query="q", knowledge_base_id="kb-1", tenant_id="t-1", user_id="u-1")
    return RoutedAgentState(**(base | kwargs))


@pytest.mark.parametrize("route", list(Route))
def test_select_branch_dispatches_low_confidence_route(route):
    # Before the fix, any confidence below the threshold was forced to LOOKUP here.
    branch = select_branch(_state(route=route.value, confidence=0.1))
    assert branch == {
        Route.LOOKUP: "lookup",
        Route.GLOBAL: "global",
        Route.METADATA: "metadata",
        Route.MULTI_HOP: "multi_hop",
        Route.NO_RETRIEVAL: "no_retrieval",
        Route.META: "meta",
    }[route]


def test_select_branch_defaults_to_lookup_when_unrouted():
    assert select_branch(_state()) == "lookup"


def test_router_node_writes_lookup_for_unaccepted_decision():
    router = _Fixed(RouteDecision(route=Route.GLOBAL, confidence=0.5, accepted=False))
    update = asyncio.run(RouterNode(router=router, fallback=_Raises())(_state()))

    assert update["route"] == DEFAULT_ROUTE.value
    assert update["confidence"] == 0.5
    assert any("not accepted" in entry for entry in update["trace"])


def test_router_node_keeps_accepted_low_confidence_decision():
    router = _Fixed(RouteDecision(route=Route.MULTI_HOP, confidence=0.3, accepted=True))
    update = asyncio.run(RouterNode(router=router, fallback=_Raises())(_state()))

    assert update["route"] == Route.MULTI_HOP.value


def test_router_node_with_noop_router_routes_lookup():
    update = asyncio.run(RouterNode(router=NoOpRouter(), fallback=_Raises())(_state()))
    assert update["route"] == DEFAULT_ROUTE.value


def test_speculative_executor_honours_accepted_llm_decision():
    dispatched: list[str] = []

    async def dispatch(route_value, state):
        dispatched.append(route_value)
        return {"route": route_value}

    router = _Fixed(RouteDecision(route=Route.GLOBAL, confidence=0.3, accepted=True))
    result = asyncio.run(SpeculativeExecutor(router=router, dispatch=dispatch).run("q", _CTX, _state()))

    assert result.branch_result == {"route": Route.GLOBAL.value}
    assert not result.speculation_hit
