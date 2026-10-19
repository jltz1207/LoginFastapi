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
        self.map_error: BaseException | None = None  # raised by map batches containing "boom"

    async def ainvoke(self, messages):
        system = messages[0].content
        if system == _MAP_SYSTEM:
            self.calls.append("map")
            if self.map_error is not None and "boom" in messages[1].content:
                raise self.map_error
            return AIMessage(content="partial")
        assert system == _ANSWER_SYSTEM
        self.calls.append("answer")
        return AIMessage(content="final answer")


@pytest.fixture
def llm(monkeypatch):
    fake = _FakeLLM()
    monkeypatch.setattr(global_summary.LLMFactory, "get_model", lambda *a, **kw: fake)
    return fake


def _run(monkeypatch, doc_count: int, batch_size: int = 8, failing_docs: int = 0) -> dict:
    # The first `failing_docs` summaries contain "boom", so the map batches holding them fail.
    summaries = [
        Chunk(chunk_id=f"d-{i}", content=f"boom {i}" if i < failing_docs else f"summary {i}")
        for i in range(doc_count)
    ]

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


def test_partial_map_failure_is_reported_not_silent(monkeypatch, llm):
    llm.map_error = RuntimeError("quota exceeded")
    # 50 docs / batch 8 → 7 batches; the first batch fails (1/7 ≤ 0.5).
    result = _run(monkeypatch, 50, failing_docs=8)
    assert llm.calls[-1] == "answer"
    assert result["answer"].startswith("final answer")
    assert result["answer"].endswith(global_summary._PARTIAL_FAILURE_NOTICE)
    assert "failed_batches=1" in result["trace"][-1]


def test_no_failure_adds_no_notice(monkeypatch, llm):
    result = _run(monkeypatch, 50)
    assert result["answer"] == "final answer"
    assert "failed_batches=0" in result["trace"][-1]


def test_failure_ratio_above_threshold_raises(monkeypatch, llm):
    llm.map_error = RuntimeError("quota exceeded")
    # 4 of 7 batches fail (4/7 > 0.5).
    with pytest.raises(RuntimeError, match="4/7 map batches failed"):
        _run(monkeypatch, 50, failing_docs=32)
    assert "answer" not in llm.calls


def test_all_map_batches_failing_raises(monkeypatch, llm):
    llm.map_error = RuntimeError("quota exceeded")
    with pytest.raises(RuntimeError, match="all map batches failed"):
        _run(monkeypatch, 50, failing_docs=50)


@pytest.mark.parametrize("error", [asyncio.CancelledError(), KeyboardInterrupt()])
def test_non_exception_base_errors_propagate(monkeypatch, llm, error):
    llm.map_error = error
    with pytest.raises(type(error)):
        _run(monkeypatch, 50, failing_docs=8)
    assert "answer" not in llm.calls


def test_invalid_max_failure_ratio_rejected():
    with pytest.raises(ValueError):
        GlobalSummaryBranch(max_failure_ratio=1.5)
