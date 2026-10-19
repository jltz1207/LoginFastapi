"""GLOBAL branch map-reduce: the last LLM call always answers the question with
GLOBAL_SUMMARY_ANSWER_PROMPT; MAP_PROMPT only condenses while summaries do not fit one call.

A fake LLM records the prompts it receives, and `fetch_document_summaries` is patched,
so neither the DB nor Gemini is touched.
"""

import asyncio

import pytest
from langchain_core.messages import AIMessage

from app.agent.state import RoutedAgentState
from app.rag.prompts import GLOBAL_SUMMARY_ANSWER_PROMPT, GLOBAL_SUMMARY_MAP_PROMPT
from app.routing.branches import global_summary
from app.routing.branches.common import Chunk
from app.routing.branches.global_summary import GlobalSummaryBranch

_MAP_SYSTEM = GLOBAL_SUMMARY_MAP_PROMPT.format_messages(query="", summaries="")[0].content
_ANSWER_SYSTEM = GLOBAL_SUMMARY_ANSWER_PROMPT.format_messages(query="", summaries="")[0].content


class _FakeLLM:
    def __init__(self):
        self.calls: list[str] = []  # "map" / "answer", in call order

    async def ainvoke(self, messages):
        system = messages[0].content
        if system == _MAP_SYSTEM:
            self.calls.append("map")
            return AIMessage(content="partial")
        assert system == _ANSWER_SYSTEM
        self.calls.append("answer")
        return AIMessage(content="final answer")


@pytest.fixture
def llm(monkeypatch):
    fake = _FakeLLM()
    monkeypatch.setattr(global_summary.LLMFactory, "get_model", lambda *a, **kw: fake)
    return fake


def _run(monkeypatch, doc_count: int, batch_size: int = 8) -> dict:
    summaries = [Chunk(chunk_id=f"d-{i}", content=f"summary {i}") for i in range(doc_count)]

    async def fake_fetch(tenant_id, user_id, knowledge_base_id):
        return summaries

    monkeypatch.setattr(global_summary, "fetch_document_summaries", fake_fetch)
    state = RoutedAgentState(
        user_id="u-1",
        tenant_id="t-1",
        knowledge_base_id="kb-1",
        query="這個知識庫在講什麼？",
        resolved_query="這個知識庫在講什麼？",
    )
    return asyncio.run(GlobalSummaryBranch(batch_size=batch_size)(state))


def test_single_document_is_answered_directly(monkeypatch, llm):
    result = _run(monkeypatch, 1)
    assert llm.calls == ["answer"]
    assert result["answer"] == "final answer"


def test_batch_size_documents_skip_the_map_step(monkeypatch, llm):
    _run(monkeypatch, 8, batch_size=8)
    assert llm.calls == ["answer"]


def test_many_documents_map_then_answer_once(monkeypatch, llm):
    result = _run(monkeypatch, 50, batch_size=8)
    # 50 → 7 intermediate summaries (≤ 8) → 1 answer call.
    assert llm.calls == ["map"] * 7 + ["answer"]
    assert result["answer"] == "final answer"


def test_answer_prompt_receives_question_and_summaries():
    messages = GLOBAL_SUMMARY_ANSWER_PROMPT.format_messages(query="Q?", summaries="S1\n\nS2")
    assert "Q?" in messages[1].content
    assert "S1\n\nS2" in messages[1].content
