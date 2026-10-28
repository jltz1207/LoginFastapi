"""SpeculativeExecutor: speculation must never cost more than not speculating.

No LLM or retrieval is touched: router and dispatch are fakes.
"""

import asyncio

import pytest

from app.agent.state import RoutedAgentState
from app.routing.router.base import BaseRouter
from app.routing.router.speculative import SpeculativeExecutor
from app.routing.taxonomy import Route, RouteDecision, RouterContext

_CTX = RouterContext(tenant_id="t-1", knowledge_base_id="kb-1")


def _state():
    return RoutedAgentState(tenant_id="t-1", user_id="u-1", knowledge_base_id="kb-1", query="q", resolved_query="q")


class _Router(BaseRouter):
    def __init__(self, route, delay=0.0, error=None):
        self._route = route
        self._delay = delay
        self._error = error

    async def route(self, query, ctx):
        await asyncio.sleep(self._delay)
        if self._error:
            raise self._error
        return RouteDecision(route=self._route, confidence=0.9, accepted=True)


class _Dispatch:
    """LOOKUP is slow; every other route is instant. Records what ran and what was cancelled."""

    def __init__(self, lookup_seconds=0.5):
        self.started: list[str] = []
        self.cancelled: list[str] = []
        self._lookup_seconds = lookup_seconds

    async def __call__(self, route_value, state):
        self.started.append(route_value)
        try:
            if route_value == Route.LOOKUP.value:
                await asyncio.sleep(self._lookup_seconds)
            return {"route": route_value}
        except asyncio.CancelledError:
            self.cancelled.append(route_value)
            raise


def test_hit_reuses_the_speculative_result():
    dispatch = _Dispatch(lookup_seconds=0.01)
    executor = SpeculativeExecutor(router=_Router(Route.LOOKUP), dispatch=dispatch)

    result = asyncio.run(executor.run("q", _CTX, _state()))

    assert result.speculation_hit
    assert result.branch_result == {"route": Route.LOOKUP.value}
    assert dispatch.started == [Route.LOOKUP.value]


def test_miss_cancels_speculation_instead_of_waiting_for_it():
    dispatch = _Dispatch(lookup_seconds=5.0)
    executor = SpeculativeExecutor(router=_Router(Route.GLOBAL, delay=0.01), dispatch=dispatch)

    async def run():
        result = await asyncio.wait_for(executor.run("q", _CTX, _state()), timeout=1.0)
        # Checked before asyncio.run() tears the loop down: the cancel must already be done.
        assert dispatch.cancelled == [Route.LOOKUP.value]
        return result

    result = asyncio.run(run())

    assert not result.speculation_hit
    assert result.branch_result == {"route": Route.GLOBAL.value}
    assert result.total_latency_seconds < 1.0
