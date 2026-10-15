"""DocumentSummarizer map-reduce, with a fake chat model that records every call."""

import asyncio

import pytest
from langchain_core.messages import AIMessage

from app.ingestion.summarizer import MAX_SUMMARY_CHARS, MIN_SUMMARY_CHARS, DocumentSummarizer, target_summary_chars
from app.rag.prompts import DOCUMENT_SUMMARY_MAP_PROMPT, DOCUMENT_SUMMARY_REDUCE_PROMPT

MAP_MARKER = DOCUMENT_SUMMARY_MAP_PROMPT.messages[0].prompt.template.strip().splitlines()[0]
REDUCE_MARKER = DOCUMENT_SUMMARY_REDUCE_PROMPT.messages[0].prompt.template.strip().splitlines()[0]


class FakeModel:
    def __init__(self, reply_chars: int = 50, reply: str | None = None):
        self.calls: list[tuple[str, str]] = []  # (kind, human message)
        self._reply_chars = reply_chars
        self._reply = reply

    async def ainvoke(self, messages):
        system, human = messages[0].content, messages[-1].content
        kind = "map" if MAP_MARKER in system else "reduce" if REDUCE_MARKER in system else "?"
        self.calls.append((kind, human))
        if self._reply is not None:
            return AIMessage(content=self._reply)
        return AIMessage(content=f"{kind}#{len(self.calls)} " + "s" * self._reply_chars)

    def kinds(self) -> list[str]:
        return [kind for kind, _ in self.calls]


def _summarizer(model, **kwargs):
    kwargs.setdefault("section_chars", 1000)
    kwargs.setdefault("batch_size", 3)
    kwargs.setdefault("max_concurrency", 2)
    return DocumentSummarizer(get_model=lambda: model, **kwargs)


def _paragraphs(n: int, size: int = 900) -> str:
    return "\n\n".join(f"P{i} " + "x" * size for i in range(n))


def test_short_document_is_one_reduce_call():
    model = FakeModel()

    summary = asyncio.run(_summarizer(model).summarize("A short memo about the Q3 budget."))

    assert model.kinds() == ["reduce"]
    assert "A short memo about the Q3 budget." in model.calls[0][1]
    assert summary.startswith("reduce#1")


def test_long_document_maps_each_section_then_reduces():
    model = FakeModel(reply_chars=50)

    asyncio.run(_summarizer(model).summarize(_paragraphs(4)))

    # 4 sections fit under section_chars once summarized → 4 maps, 1 reduce.
    assert model.kinds() == ["map"] * 4 + ["reduce"]
    mapped = [human for kind, human in model.calls if kind == "map"]
    assert [m.split("<text>")[1].strip()[:2] for m in mapped] == ["P0", "P1", "P2", "P3"]
    reduce_input = model.calls[-1][1]
    assert all(f"map#{i}" in reduce_input for i in range(1, 5))


def test_partials_too_long_for_one_call_are_collapsed_first():
    # 7 sections, each partial ~400 chars: 7 * 400 > 1000, so collapse in batches of 3.
    model = FakeModel(reply_chars=400)

    asyncio.run(_summarizer(model).summarize(_paragraphs(7)))

    kinds = model.kinds()
    assert kinds[:7] == ["map"] * 7
    assert kinds[-1] == "reduce"
    assert kinds[7:-1] and set(kinds[7:-1]) == {"map"}  # at least one collapse round
    assert len(model.calls[-1][1]) < len(_paragraphs(7))


def test_empty_llm_reply_is_an_error():
    with pytest.raises(ValueError):
        asyncio.run(_summarizer(FakeModel(reply="   ")).summarize("text"))


def test_empty_document_is_an_error():
    model = FakeModel()

    with pytest.raises(ValueError):
        asyncio.run(_summarizer(model).summarize("   \n "))
    assert model.calls == []


def test_target_length_scales_with_document_size():
    assert target_summary_chars(2_000) == MIN_SUMMARY_CHARS
    assert target_summary_chars(60_000) == 600
    assert target_summary_chars(5_000_000) == MAX_SUMMARY_CHARS


def test_batch_size_below_two_is_rejected():
    with pytest.raises(ValueError):
        DocumentSummarizer(get_model=lambda: FakeModel(), batch_size=1)
