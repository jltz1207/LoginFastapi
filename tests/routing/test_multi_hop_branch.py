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


def test_chunks_keep_document_metadata():
    branch = MultiHopBranch(
        decomposer=_ScriptedDecomposer([["A 的規格"]]), retriever=_retriever(), synthesizer=_StubSynthesizer()
    )

    update = asyncio.run(branch(_state()))

    (chunk,) = update["documents"]
    assert chunk.metadata == {"document_id": "d-1"}


class _RecordingStructuredModel:
    def __init__(self, rounds):
        self._rounds = list(rounds)
        self.calls: list = []

    async def ainvoke(self, messages, config=None):
        from app.routing.branches.multi_hop import _DecomposeOutput

        self.calls.append(messages)
        return _DecomposeOutput(sub_queries=self._rounds.pop(0) if self._rounds else [])


def test_decomposer_sees_retrieved_excerpts():
    from app.agent.state import Chunk
    from app.routing.branches.multi_hop import LLMQueryDecomposer

    model = _RecordingStructuredModel([["誰負責專案 X"]])
    decomposer = LLMQueryDecomposer(structured_model=model)
    hops = [
        HopResult(
            sub_query="專案 X 的負責人",
            chunks=[Chunk(chunk_id="c1", content="專案 X 由王小明負責。" + "很長" * 500)],
        )
    ]

    result = asyncio.run(decomposer.decompose("王小明還負責哪些專案？", hops))

    assert result == ["誰負責專案 X"]
    human = model.calls[0][-1].content
    assert "專案 X 由王小明負責。" in human
    assert "很長" * 200 not in human  # excerpt is truncated


def test_duplicate_sub_queries_in_one_round_are_retrieved_once():
    calls = []
    branch = MultiHopBranch(
        decomposer=_ScriptedDecomposer([["台北天氣", "台北 天氣", "台北天氣 "]]),
        retriever=_retriever(calls),
        synthesizer=_StubSynthesizer(),
    )

    asyncio.run(branch(_state()))

    assert calls == ["台北天氣"]


class _HangsAfterFirstRetriever:
    def __init__(self):
        self.calls = 0

    async def ainvoke(self, query):
        self.calls += 1
        if self.calls > 1:
            await asyncio.sleep(10)
        return [Document(id=f"doc:{query}", page_content=f"content of {query}")]


class _RecordingSynthesizer:
    def __init__(self):
        self.hops = None

    async def synthesize(self, query, hops):
        self.hops = hops
        return "partial answer"


def test_timeout_keeps_completed_hops():
    synthesizer = _RecordingSynthesizer()
    branch = MultiHopBranch(
        decomposer=_ScriptedDecomposer([["A"], ["B"]]),
        retriever=_HangsAfterFirstRetriever(),
        synthesizer=synthesizer,
        global_timeout_seconds=0.2,
    )

    update = asyncio.run(branch(_state()))

    assert [h.sub_query for h in synthesizer.hops] == ["A"]
    assert update["answer"] == "partial answer"
    assert [c.chunk_id for c in update["documents"]] == ["doc:A"]
    assert "global_timeout" in update["trace"][-1]


def test_recursion_limit_is_recorded():
    branch = MultiHopBranch(
        decomposer=_ScriptedDecomposer([["A"], ["B"], ["C"]]),
        retriever=_retriever(),
        synthesizer=_StubSynthesizer(),
        recursion_limit=2,
    )

    update = asyncio.run(branch(_state()))

    assert len(update["documents"]) == 2
    assert "recursion_limit" in update["trace"][-1]


def test_default_retriever_is_hybrid_with_rerank(monkeypatch):
    from app.routing.branches import multi_hop

    built = []

    class _FakeHybrid:
        def __init__(self, weights):
            built.append(("hybrid", weights))

        def get_retriever(self, tenant_id, user_id, knowledge_base_id, top_k):
            built.append(("scope", tenant_id, user_id, knowledge_base_id, top_k))
            return _retriever()

    def fake_with_rerank(retriever, rerank_top_k):
        built.append(("rerank", rerank_top_k))
        return retriever

    monkeypatch.setattr(multi_hop, "HybridRetriever", _FakeHybrid)
    monkeypatch.setattr(multi_hop, "with_rerank", fake_with_rerank)
    branch = MultiHopBranch(decomposer=_ScriptedDecomposer([["A"]]), synthesizer=_StubSynthesizer())

    asyncio.run(branch(_state()))

    assert built == [("hybrid", (0.6, 0.4)), ("scope", "t-1", "u-1", "kb-1", 4), ("rerank", 4)]
