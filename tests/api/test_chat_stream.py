"""POST /chat: SSE output and what gets persisted, for both graph strategies.

The endpoint function is called directly with asyncio.run, a fake AsyncSession and a
fake compiled graph that replays a fixed `astream_events` sequence (no LLM, no DB).
"""

import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from app.agent.state import Chunk
from app.api.v1.endpoints import chats as chat_endpoint
from app.models.asistantMessage import RoleEnum
from app.schemas.chats import ChatRequestModel


class FakeSession:
    def __init__(self):
        self.added = []
        self.committed = False

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def execute(self, stmt):
        return SimpleNamespace(scalar=lambda: 0)


class FakeGraph:
    def __init__(self, events, error=None):
        self._events = events
        self._error = error
        self.inputs = []

    async def astream_events(self, input, config, version):
        self.inputs.append(input)
        for event in self._events:
            yield event
        if self._error:
            raise self._error


def chain_start(name):
    return {"event": "on_chain_start", "name": name, "metadata": {"langgraph_node": name}, "tags": [], "data": {}}


def chain_end(name, output):
    return {
        "event": "on_chain_end",
        "name": name,
        "metadata": {"langgraph_node": name},
        "tags": [],
        "data": {"output": output},
    }


def token(node, text, tags=()):
    return {
        "event": "on_chat_model_stream",
        "name": "model",
        "metadata": {"langgraph_node": node},
        "tags": list(tags),
        "data": {"chunk": AIMessageChunk(content=text)},
    }


def model_end(node, message, tags=()):
    return {
        "event": "on_chat_model_end",
        "name": "model",
        "metadata": {"langgraph_node": node},
        "tags": list(tags),
        "data": {"output": message},
    }


@pytest.fixture
def user():
    return SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4())


@pytest.fixture(autouse=True)
def owned_kb(monkeypatch):
    async def fake(db, knowledge_base_id, current_user):
        return SimpleNamespace(id=knowledge_base_id)

    monkeypatch.setattr(chat_endpoint, "get_owned_knowledge_base", fake)


def run_chat(user, graph, question="退款政策是什麼？"):
    db = FakeSession()
    request = ChatRequestModel(question=question, knowledge_base_id=uuid.uuid4())

    async def run():
        response = await chat_endpoint.chat(request, db=db, current_user=user, graph=graph)
        frames = [frame async for frame in response.body_iterator]
        return [json.loads(f.removeprefix("data: ").strip()) for f in frames]

    events = asyncio.run(run())
    return events, db


def _messages(db):
    return {m.role: m for m in db.added}


def test_lookup_stream_persists_both_turns_with_sources(user):
    docs = [Chunk(chunk_id="d:0", content="退款需在七天內申請。", metadata={"document_id": "d"})]
    graph = FakeGraph(
        [
            chain_start("retrieve_docs"),
            chain_end("retrieve_docs", {"documents": docs}),
            chain_start("generate"),
            token("generate", "七天"),
            token("generate", "內。"),
            chain_end("generate", {"chat_history": [AIMessage("七天內。")], "model_used": "m"}),
        ]
    )

    events, db = run_chat(user, graph)

    assert events[-1] == {"type": "done"}
    assert "".join(e["content"] for e in events if e["type"] == "token") == "七天內。"
    ai = _messages(db)[RoleEnum.AI]
    assert ai.content == "七天內。"
    assert "退款需在七天內申請。" in ai.sources


@pytest.mark.parametrize("strategy,state_class", [(2, "LookupAgentState"), (4, "RoutedAgentState")])
def test_input_state_matches_the_graph_strategy(monkeypatch, user, strategy, state_class):
    from app.agent import dependencies

    monkeypatch.setattr(dependencies.settings, "GRAPH_STRATEGY", strategy)
    graph = FakeGraph([])

    run_chat(user, graph, question="  你好  ")

    (state,) = graph.inputs
    assert type(state).__name__ == state_class
    assert state.query == "你好"
    assert [(m.type, m.content) for m in state.chat_history] == [("human", "你好")]
