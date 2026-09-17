import asyncio

from langchain_core.documents import Document
from langchain_core.runnables import RunnableLambda

from app.agent.state import RoutedAgentState
from app.routing.branches import multi_hop
from app.routing.branches.multi_hop import MultiHopBranch


class _OneHopDecomposer:
    async def decompose(self, query, hops_so_far):
        return [] if hops_so_far else [query]


class _StubSynthesizer:
    async def synthesize(self, query, hops):
        return "stub answer"


def _state(tenant_id: str, user_id: str, knowledge_base_id: str) -> RoutedAgentState:
    return RoutedAgentState(
        tenant_id=tenant_id,
        user_id=user_id,
        knowledge_base_id=knowledge_base_id,
        query="q",
        resolved_query="q",
    )


def test_retriever_is_scoped_per_request_not_cached_on_shared_instance(monkeypatch):
    calls: list[tuple[str, str, str]] = []

    async def fake_get_retriever(self, tenant_id, user_id, knowledge_base_id, top_k=8):
        calls.append((tenant_id, user_id, knowledge_base_id))
        return RunnableLambda(
            lambda query: [Document(id=f"{tenant_id}-chunk", page_content=f"{tenant_id}:{query}")]
        )

    monkeypatch.setattr(multi_hop.BasicRetriever, "get_retriever", fake_get_retriever)

    # One instance serving two tenants, mirroring the module-level `agentic_subgraph`.
    branch = MultiHopBranch(decomposer=_OneHopDecomposer(), synthesizer=_StubSynthesizer())

    first = asyncio.run(branch(_state("tenant-a", "user-a", "kb-a")))
    second = asyncio.run(branch(_state("tenant-b", "user-b", "kb-b")))

    assert calls == [("tenant-a", "user-a", "kb-a"), ("tenant-b", "user-b", "kb-b")]
    assert [c.chunk_id for c in first["documents"]] == ["tenant-a-chunk"]
    assert [c.chunk_id for c in second["documents"]] == ["tenant-b-chunk"]
