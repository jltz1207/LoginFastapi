"""NO_RETRIEVAL branch: zero retrieval, direct answer from the LLM.

No LLM is touched: the model is a fake that records the messages it was given.
"""

import asyncio

from langchain_core.messages import AIMessage

from app.agent.state import RoutedAgentState
from app.routing.branches.no_retrieval import LLMDirectAnswerLLM, NoRetrievalBranch


class _FakeModel:
    def __init__(self, content="巴黎。", total_tokens=42):
        self.calls: list = []
        self._content = content
        self._total_tokens = total_tokens

    async def ainvoke(self, messages, config=None):
        self.calls.append(messages)
        return AIMessage(
            content=self._content,
            usage_metadata={"input_tokens": 30, "output_tokens": 12, "total_tokens": self._total_tokens},
        )


def _state(query="法國的首都是哪裡？"):
    return RoutedAgentState(
        query=query,
        resolved_query=query,
        tenant_id="t-1",
        user_id="u-1",
        knowledge_base_id="kb-1",
    )


def test_answers_with_system_prompt_and_no_documents():
    model = _FakeModel()
    branch = NoRetrievalBranch(llm=LLMDirectAnswerLLM(model=model))

    update = asyncio.run(branch(_state()))

    assert update["answer"] == "巴黎。"
    assert update["documents"] == []
    (messages,) = model.calls
    assert [m.type for m in messages] == ["system", "human"]
    assert "法國的首都是哪裡？" in messages[1].content
    assert "knowledge base" in messages[0].content


def test_default_model_comes_from_llm_factory(monkeypatch):
    from app.llm.factory import LLMFactory

    model = _FakeModel()
    monkeypatch.setattr(LLMFactory, "get_model", classmethod(lambda cls, tools=None: model))

    update = asyncio.run(NoRetrievalBranch(llm=LLMDirectAnswerLLM())(_state()))

    assert update["answer"] == "巴黎。"
    assert len(model.calls) == 1
