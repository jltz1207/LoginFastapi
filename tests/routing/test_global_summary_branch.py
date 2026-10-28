"""GLOBAL branch map-reduce: the last LLM call always answers the question with
GLOBAL_SUMMARY_ANSWER_PROMPT; MAP_PROMPT only condenses while summaries do not fit one call.

A fake LLM records the prompts it receives, and `fetch_document_summaries` is patched,
so neither the DB nor Gemini is touched.
"""

import asyncio
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from app.agent.state import RoutedAgentState
from app.rag.prompts import GLOBAL_SUMMARY_ANSWER_PROMPT, GLOBAL_SUMMARY_MAP_PROMPT
from app.routing.branches import global_summary
from app.routing.branches.common import Chunk, format_citations
from app.routing.branches.global_summary import GlobalSummaryBranch

_MAP_SYSTEM = GLOBAL_SUMMARY_MAP_PROMPT.format_messages(query="", summaries="")[0].content
_ANSWER_SYSTEM = GLOBAL_SUMMARY_ANSWER_PROMPT.format_messages(query="", summaries="")[0].content


_USAGE = {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}  # per successful call


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
            return AIMessage(content="partial", usage_metadata=_USAGE)
        assert system == _ANSWER_SYSTEM
        self.calls.append("answer")
        return AIMessage(content="final answer", usage_metadata=_USAGE)


@pytest.fixture
def llm(monkeypatch):
    fake = _FakeLLM()
    monkeypatch.setattr(global_summary.LLMFactory, "get_model", lambda *a, **kw: fake)
    return fake


def _run(
    monkeypatch, doc_count: int, batch_size: int = 8, failing_docs: int = 0, max_documents: int = 200, token_count: int = 0
) -> dict:
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
        token_count=token_count,
    )
    return asyncio.run(GlobalSummaryBranch(batch_size=batch_size, max_documents=max_documents)(state))


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


def test_documents_past_max_documents_are_left_out(monkeypatch, llm):
    result = _run(monkeypatch, 50, max_documents=16)
    # Only 16 summaries reach map-reduce: 2 map batches + 1 answer call.
    assert llm.calls == ["map"] * 2 + ["answer"]
    assert len(result["documents"]) == 16
    assert result["answer"].endswith(global_summary._TRUNCATED_NOTICE.format(used=16, total=50))
    assert "docs=16/50" in result["trace"][-1]


def test_within_max_documents_adds_no_truncation_notice(monkeypatch, llm):
    result = _run(monkeypatch, 16, max_documents=16)
    assert result["answer"] == "final answer"
    assert "docs=16/16" in result["trace"][-1]


def test_token_count_sums_every_successful_call(monkeypatch, llm):
    result = _run(monkeypatch, 50)
    # 7 map calls + 1 answer call, 10 tokens each.
    assert result["token_count"] == 80
    assert "tokens=80" in result["trace"][-1]


def test_token_count_skips_failed_calls(monkeypatch, llm):
    llm.map_error = RuntimeError("quota exceeded")
    result = _run(monkeypatch, 50, failing_docs=8)
    # The failed map batch reports no usage: 6 map + 1 answer.
    assert result["token_count"] == 70


def test_token_count_is_absolute_per_run(monkeypatch, llm):
    # A previous turn's checkpointed total must not carry over into this run.
    result = _run(monkeypatch, 1, token_count=1000)
    assert result["token_count"] == 10


def test_invalid_max_documents_rejected():
    with pytest.raises(ValueError):
        GlobalSummaryBranch(max_documents=0)


def test_guard_size_caps_concurrency_across_requests(monkeypatch):
    in_flight = peak = 0

    class _SlowLLM:
        async def ainvoke(self, messages):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1
            return AIMessage(content="partial", usage_metadata=_USAGE)

    async def fake_fetch(tenant_id, user_id, knowledge_base_id):
        return [Chunk(chunk_id=f"d-{i}", content=f"summary {i}") for i in range(40)]

    monkeypatch.setattr(global_summary.LLMFactory, "get_model", lambda *a, **kw: _SlowLLM())
    monkeypatch.setattr(global_summary, "fetch_document_summaries", fake_fetch)
    state = RoutedAgentState(
        user_id="u-1", tenant_id="t-1", knowledge_base_id="kb-1", query="Q?", resolved_query="Q?"
    )
    # One shared instance, as with the module-level `global_summary_node`. Each request
    # alone fires 10 map batches at once, so a per-request semaphore would reach 6 + 6.
    branch = GlobalSummaryBranch(batch_size=4, guard_size=6)

    async def two_requests():
        await asyncio.gather(branch(state), branch(state))

    asyncio.run(two_requests())
    assert peak == 6


def test_invalid_guard_size_is_rejected():
    with pytest.raises(ValueError):
        GlobalSummaryBranch(guard_size=0)


def test_fetched_summaries_cite_filename_with_str_document_id(monkeypatch):
    doc_id = uuid.uuid4()
    rows = [SimpleNamespace(id=doc_id, filename="handbook.pdf", summary="Topics: onboarding")]

    class _FakeSession:
        async def execute(self, stmt):
            return SimpleNamespace(all=lambda: rows)

    @asynccontextmanager
    async def fake_db_ctx():
        yield _FakeSession()

    monkeypatch.setattr(global_summary, "get_db_ctx", fake_db_ctx)
    chunks = asyncio.run(
        global_summary.fetch_document_summaries(str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()))
    )
    assert chunks[0].source == "handbook.pdf"
    assert chunks[0].metadata == {"document_id": str(doc_id)}
    assert format_citations(chunks) == "[1] handbook.pdf"


def test_fetched_summaries_are_ordered_by_created_dt_then_id(monkeypatch):
    executed = []

    class _FakeSession:
        async def execute(self, stmt):
            executed.append(stmt)
            return SimpleNamespace(all=lambda: [])

    @asynccontextmanager
    async def fake_db_ctx():
        yield _FakeSession()

    monkeypatch.setattr(global_summary, "get_db_ctx", fake_db_ctx)
    asyncio.run(
        global_summary.fetch_document_summaries(str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()))
    )
    order_by = [str(clause) for clause in executed[0]._order_by_clauses]
    assert order_by == ["documents.created_dt", "documents.id"]


def test_fetched_summaries_exclude_null_and_empty(monkeypatch):
    executed = []

    class _FakeSession:
        async def execute(self, stmt):
            executed.append(stmt)
            return SimpleNamespace(all=lambda: [])

    @asynccontextmanager
    async def fake_db_ctx():
        yield _FakeSession()

    monkeypatch.setattr(global_summary, "get_db_ctx", fake_db_ctx)
    asyncio.run(
        global_summary.fetch_document_summaries(str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()))
    )
    where = [str(clause) for clause in executed[0].whereclause.clauses]
    assert "documents.summary IS NOT NULL" in where
    assert "documents.summary != :summary_1" in where


def test_llm_sees_summaries_labelled_with_filename(monkeypatch):
    received: list[str] = []

    class _RecordingLLM:
        async def ainvoke(self, messages):
            received.append(messages[1].content)
            return AIMessage(content="final answer", usage_metadata=_USAGE)

    async def fake_fetch(tenant_id, user_id, knowledge_base_id):
        return [Chunk(chunk_id="d-1", content="Topics: onboarding", source="handbook.pdf")]

    monkeypatch.setattr(global_summary.LLMFactory, "get_model", lambda *a, **kw: _RecordingLLM())
    monkeypatch.setattr(global_summary, "fetch_document_summaries", fake_fetch)
    state = RoutedAgentState(
        user_id="u-1", tenant_id="t-1", knowledge_base_id="kb-1", query="q", resolved_query="q"
    )
    result = asyncio.run(GlobalSummaryBranch()(state))
    assert "[handbook.pdf]\nTopics: onboarding" in received[0]
    assert result["documents"][0].source == "handbook.pdf"
