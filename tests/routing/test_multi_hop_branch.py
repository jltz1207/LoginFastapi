"""MULTI_HOP branch: decomposer / synthesizer wiring and the four guardrails.

No LLM, Chroma or DB is touched: decomposer, retriever and models are fakes.
"""

import asyncio

from langchain_core.documents import Document
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from app.agent.state import RoutedAgentState
from app.routing.branches.multi_hop import HopResult, LLMMultiHopSynthesizer, MultiHopBranch


class _FakeModel:
    def __init__(self, content="answer", total_tokens=10):
        self.calls: list = []
        self._content = content
        self._total_tokens = total_tokens

    async def ainvoke(self, messages, config=None):
        self.calls.append(messages)
        return AIMessage(
            content=self._content,
            usage_metadata={"input_tokens": 0, "output_tokens": 0, "total_tokens": self._total_tokens},
        )


class _ScriptedDecomposer:
    """Returns the scripted sub-queries round by round, then [] (converged)."""

    def __init__(self, rounds):
        self._rounds = list(rounds)
        self.seen_hops: list[list[HopResult]] = []

    async def decompose(self, query, hops_so_far):
        self.seen_hops.append(list(hops_so_far))
        return self._rounds.pop(0) if self._rounds else []


class _StubSynthesizer:
    async def synthesize(self, query, hops):
        return "stub answer"


def _retriever(calls=None, delay=0.0):
    async def run(query):
        if calls is not None:
            calls.append(query)
        if delay:
            await asyncio.sleep(delay)
        return [Document(id=f"doc:{query}", page_content=f"content of {query}", metadata={"document_id": "d-1"})]

    return RunnableLambda(run)


def _state(query="比較 A 和 B"):
    return RoutedAgentState(
        tenant_id="t-1", user_id="u-1", knowledge_base_id="kb-1", query=query, resolved_query=query
    )


def test_synthesizer_uses_injected_model():
    model = _FakeModel(content="synthesized")
    synthesizer = LLMMultiHopSynthesizer(model=model)

    answer = asyncio.run(synthesizer.synthesize("q", [HopResult(sub_query="a", chunks=[])]))

    assert answer == "synthesized"
    assert len(model.calls) == 1
