"""Routers are shared across requests (P1-4): caches must survive, per-request state must not leak.

The graph is rebuilt on every request, so a router created per request re-embedded all
36 route examples each time. Default routers now come from `router/defaults.py`.

No Gemini or embeddings API is touched: embeddings and the structured LLM are fakes.
"""

import asyncio
import random

import pytest

from app.agent.graphs.routed_rag import RouterNode
from app.agent.state import RoutedAgentState
from app.routing.router import defaults
from app.routing.router import embedding as embedding_module
from app.routing.router.base import BaseRouter
from app.routing.router.cascade import CascadeRouter
from app.routing.router.embedding import ROUTE_EXAMPLES, EmbeddingRouter
from app.routing.router.fallback_chain import FallbackChainRouter
from app.routing.router.llm import LLMRouter, _LLMRouteOutput
from app.routing.router.rule import RuleRouter
from app.routing.taxonomy import CONFIDENCE_THRESHOLD, DEFAULT_ROUTE, Route, RouteDecision, RouterContext

_CTX = RouterContext(tenant_id="t-1", knowledge_base_id="kb-1", resolved_history=[])
_EXAMPLE_COUNT = sum(len(v) for v in ROUTE_EXAMPLES.values())


class _CountingEmbeddings:
    """Deterministic fake that counts calls and embedded texts."""

    def __init__(self):
        self.calls = 0
        self.texts: list[str] = []

    async def embed(self, texts):
        self.calls += 1
        self.texts.extend(texts)
        out = []
        for text in texts:
            rnd = random.Random(text)
            out.append([rnd.gauss(0, 1) for _ in range(8)])
        return out


class _FakeStructuredLLM:
    async def ainvoke(self, messages):
        return _LLMRouteOutput(route=Route.GLOBAL)


class _Raises(BaseRouter):
    async def route(self, query, ctx):
        raise RuntimeError("boom")


def _unsure_queries(n: int) -> list[str]:
    """Queries whose best embedding similarity is below the threshold (they reach the LLM layer)."""
    probe = EmbeddingRouter(embeddings=_CountingEmbeddings())
    found = []
    for i in range(200):
        query = f"query {i}"
        if asyncio.run(probe.route(query, _CTX)).confidence < CONFIDENCE_THRESHOLD:
            found.append(query)
            if len(found) == n:
                return found
    raise AssertionError("not enough queries reach the LLM layer")


def _state() -> RoutedAgentState:
    return RoutedAgentState(query="q", resolved_query="q", knowledge_base_id="kb-1", tenant_id="t-1", user_id="u-1")


def test_default_routers_are_process_singletons_sharing_one_embedding_router():
    router, fallback = defaults.get_default_router(), defaults.get_default_fallback()
    shared = defaults.get_shared_embedding_router()

    assert defaults.get_default_router() is router
    assert defaults.get_default_fallback() is fallback
    assert router._fast_layers[1] is shared
    assert dict(fallback._layers)["embedding"] is shared
    assert defaults.get_shared_llm_router()._confidence_scorer is shared


def test_router_node_defaults_to_shared_routers():
    node_a, node_b = RouterNode(), RouterNode()

    assert node_a._router is node_b._router is defaults.get_default_router()
    assert node_a._fallback is node_b._fallback is defaults.get_default_fallback()


def test_shared_embedding_router_embeds_examples_once_and_each_query_once():
    emb = _CountingEmbeddings()
    shared = EmbeddingRouter(embeddings=emb)
    cascade = CascadeRouter(
        rule=RuleRouter(),
        embedding=shared,
        llm=LLMRouter(structured_model=_FakeStructuredLLM(), confidence_scorer=shared),
    )
    queries = _unsure_queries(3)

    for query in queries:
        decision = asyncio.run(cascade.route(query, _CTX))
        assert decision.route is Route.GLOBAL  # reached the LLM layer, which calls score_route()

    # Before P1-4 (one router per request, separate scorer): 3 * (36 + 1 + 36 + 1) = 222 texts.
    assert len(emb.texts) == _EXAMPLE_COUNT + len(queries)
    assert sorted(t for t in emb.texts if t in queries) == sorted(queries)


def test_query_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(embedding_module, "_QUERY_CACHE_SIZE", 2)
    emb = _CountingEmbeddings()
    router = EmbeddingRouter(embeddings=emb)

    for query in ["a", "b", "c", "a"]:
        asyncio.run(router.route(query, _CTX))

    assert len(router._query_vectors) == 2
    # "a" was evicted by "c", so it is embedded again
    assert [t for t in emb.texts if t in {"a", "b", "c"}] == ["a", "b", "c", "a"]


def test_failed_query_embedding_is_not_cached():
    class _FlakyEmbeddings(_CountingEmbeddings):
        fail_next = True

        async def embed(self, texts):
            if texts == ["q"] and self.fail_next:
                self.fail_next = False
                raise RuntimeError("embeddings API down")
            return await super().embed(texts)

    router = EmbeddingRouter(embeddings=_FlakyEmbeddings())
    with pytest.raises(RuntimeError):
        asyncio.run(router.route("q", _CTX))

    assert asyncio.run(router.route("q", _CTX)).route in Route


def test_llm_router_builds_structured_model_once():
    built = []

    class _FakeChatModel:
        def with_structured_output(self, schema):
            return _FakeStructuredLLM()

    def factory(cached_content):
        built.append(cached_content)
        return _FakeChatModel()

    llm = LLMRouter(model_factory=factory, confidence_scorer=EmbeddingRouter(embeddings=_CountingEmbeddings()))
    for _ in range(3):
        asyncio.run(llm.route("q", _CTX))

    assert built == [None]


def test_shared_fallback_chain_keeps_degradations_per_request():
    """Two interleaved requests on one shared chain must not see each other's degradations."""

    class _FailsFor(BaseRouter):
        def __init__(self, failing_query):
            self.failing_query = failing_query

        async def route(self, query, ctx):
            await asyncio.sleep(0)  # yield so the two requests interleave
            if query == self.failing_query:
                raise RuntimeError(f"fail {query}")
            return RouteDecision(route=Route.MULTI_HOP, confidence=0.3, accepted=True)

    accepted_meta = RouteDecision(route=Route.META, confidence=0.95, accepted=True)

    class _Rule(BaseRouter):
        async def route(self, query, ctx):
            return accepted_meta

    chain = FallbackChainRouter(llm=_FailsFor("bad"), embedding=_Raises(), rule=_Rule())

    async def both():
        return await asyncio.gather(chain.route("bad", _CTX), chain.route("good", _CTX))

    bad, good = asyncio.run(both())

    assert bad.route is Route.META
    assert [d.split(" (")[0] for d in bad.degradations] == [
        "fallback_chain: llm router failed",
        "fallback_chain: embedding router failed",
    ]
    assert good.route is Route.MULTI_HOP
    assert good.degradations == []
    assert accepted_meta.degradations == []  # the layer's own decision object is not mutated
    assert not hasattr(chain, "last_degradations")


def test_router_node_traces_cascade_llm_failure():
    unsure = RouteDecision(route=Route.GLOBAL, confidence=0.4, accepted=False)

    class _Fixed(BaseRouter):
        async def route(self, query, ctx):
            return unsure

    node = RouterNode(router=CascadeRouter(rule=_Fixed(), embedding=_Fixed(), llm=_Raises()), fallback=_Raises())
    update = asyncio.run(node(_state()))

    assert update["route"] == DEFAULT_ROUTE.value
    assert any(entry.startswith("cascade: llm router failed (RuntimeError)") for entry in update["trace"])


def test_router_node_traces_fallback_degradations():
    class _Rule(BaseRouter):
        async def route(self, query, ctx):
            return RouteDecision(route=Route.META, confidence=0.95, accepted=True)

    node = RouterNode(router=_Raises(), fallback=FallbackChainRouter(llm=_Raises(), embedding=_Raises(), rule=_Rule()))
    update = asyncio.run(node(_state()))

    assert update["route"] == Route.META.value
    fallback_entries = [e for e in update["trace"] if e.startswith("fallback_chain: ")]
    assert len(fallback_entries) == 2
