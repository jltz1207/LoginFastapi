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


def _tokens(events):
    return "".join(e["content"] for e in events if e["type"] == "token")


def test_routed_branch_streams_only_its_final_answer_call(user):
    graph = FakeGraph(
        [
            chain_start("global"),
            token("global", "MAP OUTPUT"),  # intermediate map call: not tagged
            token("global", "整體而言，", tags=["final_answer"]),
            token("global", "主題是退款。", tags=["final_answer"]),
            chain_end("global", {"answer": "整體而言，主題是退款。", "documents": []}),
            chain_end("respond", {"answer": "整體而言，主題是退款。"}),
        ]
    )

    events, db = run_chat(user, graph)

    assert _tokens(events) == "整體而言，主題是退款。"
    assert _messages(db)[RoleEnum.AI].content == "整體而言，主題是退款。"


def test_branch_without_llm_sends_the_whole_answer_once(user):
    graph = FakeGraph(
        [
            chain_start("meta"),
            chain_end("meta", {"answer": "我可以回答知識庫的問題。", "documents": []}),
            chain_end("respond", {"answer": "我可以回答知識庫的問題。"}),
        ]
    )

    events, db = run_chat(user, graph)

    assert [e for e in events if e["type"] == "token"] == [{"type": "token", "content": "我可以回答知識庫的問題。"}]
    assert _messages(db)[RoleEnum.AI].content == "我可以回答知識庫的問題。"


def test_routed_lookup_streams_generate_tokens_once(user):
    graph = FakeGraph(
        [
            token("generate", "七天"),
            token("generate", "內。"),
            chain_end("generate", {"chat_history": [AIMessage("七天內。")], "model_used": "m"}),
            chain_end("respond", {"answer": "七天內。"}),
        ]
    )

    events, db = run_chat(user, graph)

    assert _tokens(events) == "七天內。"
    assert _messages(db)[RoleEnum.AI].content == "七天內。"


def test_sources_come_from_routed_branches(user):
    summaries = [Chunk(chunk_id="d1", content="退款政策摘要", source="refund.pdf")]
    graph = FakeGraph(
        [
            chain_end("global", {"answer": "a", "documents": summaries}),
            chain_end("respond", {"answer": "a"}),
        ]
    )

    _, db = run_chat(user, graph)

    assert _messages(db)[RoleEnum.AI].sources == "Document 1: 退款政策摘要"


def test_sources_list_metadata_rows(user):
    rows = [{"id": "d1", "filename": "refund.pdf", "file_extension": "pdf"}]
    graph = FakeGraph(
        [
            chain_end("metadata", {"answer": "a", "sql_documents": rows}),
            chain_end("respond", {"answer": "a"}),
        ]
    )

    _, db = run_chat(user, graph)

    assert _messages(db)[RoleEnum.AI].sources == "Document 1: refund.pdf (d1)"


def _usage_message(input_tokens, output_tokens, model=None):
    return AIMessage(
        content="x",
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
        response_metadata={"model_name": model} if model else {},
    )


def test_token_usage_sums_every_llm_call_of_the_turn(user):
    graph = FakeGraph(
        [
            model_end("resolve", _usage_message(10, 1)),
            model_end("route", _usage_message(20, 2)),
            model_end("multi_hop", _usage_message(30, 3)),  # decompose
            model_end("multi_hop", _usage_message(40, 4, model="gemini-x"), tags=["final_answer"]),
            chain_end("respond", {"answer": "a"}),
        ]
    )

    _, db = run_chat(user, graph)

    ai = _messages(db)[RoleEnum.AI]
    assert (ai.prompt_tokens, ai.completion_tokens) == (100, 10)
    assert ai.model == "gemini-x"


def test_routed_nodes_report_status_and_route(user):
    graph = FakeGraph(
        [
            chain_start("resolve"),
            chain_start("route"),
            chain_end("route", {"route": "GLOBAL", "confidence": 0.9}),
            chain_start("global"),
            chain_end("respond", {"answer": "a"}),
        ]
    )

    events, _ = run_chat(user, graph)

    assert events[:4] == [
        {"type": "status", "node": "resolve"},
        {"type": "status", "node": "route"},
        {"type": "route", "route": "GLOBAL"},
        {"type": "status", "node": "global"},
    ]


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
