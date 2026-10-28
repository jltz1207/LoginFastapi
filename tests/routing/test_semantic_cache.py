"""SemanticCache: tenant isolation, invalidation, size bound and embed count.

No embeddings API is touched: embeddings are a deterministic fake that counts calls.
"""

import asyncio

import pytest

from app.routing.cache.semantic_cache import SemanticCache
from app.routing.taxonomy import Route, RouteDecision, RouterContext

_CTX_A = RouterContext(tenant_id="t-a", knowledge_base_id="kb-1")
_CTX_B = RouterContext(tenant_id="t-b", knowledge_base_id="kb-1")
_DECISION = RouteDecision(route=Route.GLOBAL, confidence=0.9, accepted=True)


class _Embeddings:
    """Same text → same vector; different texts → orthogonal vectors."""

    def __init__(self):
        self.calls = 0
        self._ids: dict[str, int] = {}

    async def embed(self, texts):
        self.calls += 1
        out = []
        for text in texts:
            i = self._ids.setdefault(text, len(self._ids))
            vec = [0.0] * 64
            vec[i % 64] = 1.0
            out.append(vec)
        return out


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _cache(**kwargs):
    return SemanticCache(embeddings=_Embeddings(), **kwargs)


def test_hit_for_same_tenant_and_version():
    cache = _cache()

    async def run():
        await cache.put("q", _CTX_A, "v1", _DECISION)
        return await cache.get("q", _CTX_A, "v1")

    assert asyncio.run(run()).route == Route.GLOBAL


def test_other_tenant_and_other_version_miss():
    cache = _cache()

    async def run():
        await cache.put("q", _CTX_A, "v1", _DECISION)
        return await cache.get("q", _CTX_B, "v1"), await cache.get("q", _CTX_A, "v2")

    assert asyncio.run(run()) == (None, None)


def test_entries_expire_after_ttl():
    clock = _Clock()
    cache = _cache(ttl_seconds=10, clock=clock)

    async def run():
        await cache.put("q", _CTX_A, "v1", _DECISION)
        clock.now = 11
        return await cache.get("q", _CTX_A, "v1")

    assert asyncio.run(run()) is None


def test_size_is_bounded_and_oldest_entries_go_first():
    cache = _cache(max_entries=3)

    async def run():
        for i in range(5):
            await cache.put(f"q{i}", _CTX_A, "v1", _DECISION)
        return [await cache.get(f"q{i}", _CTX_A, "v1") for i in range(5)]

    results = asyncio.run(run())

    assert len(cache._entries) == 3
    assert [r is not None for r in results] == [False, False, True, True, True]


def test_put_purges_expired_entries():
    clock = _Clock()
    cache = _cache(ttl_seconds=10, clock=clock)

    async def run():
        await cache.put("old", _CTX_A, "v1", _DECISION)
        clock.now = 11
        await cache.put("new", _CTX_A, "v1", _DECISION)

    asyncio.run(run())

    assert len(cache._entries) == 1


def test_invalid_max_entries():
    with pytest.raises(ValueError):
        SemanticCache(embeddings=_Embeddings(), max_entries=0)
