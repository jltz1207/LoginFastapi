"""META branch: router and branch share one pattern source, so a query the rule router
sends to META never falls through to the branch's default reply.

Pure string matching — no LLM, DB, or embeddings client is touched.
"""

import asyncio

import pytest

from app.agent.state import RoutedAgentState
from app.routing.branches.meta import _REPLIES, MetaBranch, reply_language
from app.routing.meta_intents import MetaIntent, classify_meta_intent
from app.routing.router.embedding import ROUTE_EXAMPLES
from app.routing.router.rule import RuleRouter
from app.routing.taxonomy import Route, RouterContext

_CTX = RouterContext(tenant_id="t-1", knowledge_base_id="kb-1", resolved_history=[])

# One representative per pattern in `META_INTENT_PATTERNS`, plus the embedding examples.
_META_QUERIES: list[tuple[str, MetaIntent]] = [
    ("你是誰？", MetaIntent.IDENTITY),
    ("你是什麼？", MetaIntent.IDENTITY),
    ("你是用什麼模型？", MetaIntent.IDENTITY),
    ("Who are you?", MetaIntent.IDENTITY),
    ("What model are you?", MetaIntent.IDENTITY),
    ("你能做什麼？", MetaIntent.CAPABILITIES),
    ("你可以幫我做什麼", MetaIntent.CAPABILITIES),
    ("你能提供哪些服務", MetaIntent.CAPABILITIES),
    ("這個系統有哪些功能", MetaIntent.CAPABILITIES),
    ("這個系統支援哪些功能？", MetaIntent.CAPABILITIES),
    ("我可以問你什麼類型的問題？", MetaIntent.CAPABILITIES),
    ("What can you help me with?", MetaIntent.CAPABILITIES),
    ("what can you do", MetaIntent.CAPABILITIES),
]


def _state(query: str, trace: list[str] | None = None, resolved_query: str | None = None) -> RoutedAgentState:
    return RoutedAgentState(
        user_id="u-1",
        tenant_id="t-1",
        knowledge_base_id="kb-1",
        query=query,
        resolved_query=query if resolved_query is None else resolved_query,
        trace=trace or [],
    )


def _run(query: str, trace: list[str] | None = None, resolved_query: str | None = None) -> dict:
    return asyncio.run(MetaBranch()(_state(query, trace, resolved_query)))


def _default(query: str) -> str:
    return _REPLIES[reply_language(query)][None]


@pytest.mark.parametrize("query,intent", _META_QUERIES)
def test_meta_query_gets_its_template(query, intent):
    assert classify_meta_intent(query) is intent
    assert _run(query)["answer"] == _REPLIES[reply_language(query)][intent]


@pytest.mark.parametrize("query,_intent", _META_QUERIES)
def test_rule_router_sends_meta_queries_to_meta(query, _intent):
    decision = asyncio.run(RuleRouter().route(query, _CTX))
    assert decision.route is Route.META


@pytest.mark.parametrize("query", ROUTE_EXAMPLES[Route.META])
def test_embedding_meta_examples_never_fall_to_default(query):
    assert _run(query)["answer"] != _default(query)


@pytest.mark.parametrize(
    "query",
    [q for route, examples in ROUTE_EXAMPLES.items() if route is not Route.META for q in examples]
    + ["這份文件提到什麼功能？", "第三章描述了系統的哪些功能需求？"],
)
def test_non_meta_queries_do_not_match(query):
    # Rule patterns short-circuit the cascade at 0.95; a false positive here misroutes a real question.
    assert classify_meta_intent(query) is None


@pytest.mark.parametrize(
    "language,banned",
    [
        # `documents` has no author column; GLOBAL has no summaries until ingestion generates them.
        ("zh", ["作者", "摘要"]),
        ("en", ["author", "summar"]),
    ],
)
def test_capabilities_reply_only_claims_supported_metadata_queries(language, banned):
    # `meta.py` must not import `metadata.py` (DB / LLM deps), so this test keeps the text in sync.
    from app.routing.branches.metadata import _ALLOWED_FILE_EXTENSIONS

    reply = _REPLIES[language][MetaIntent.CAPABILITIES]
    for ext in _ALLOWED_FILE_EXTENSIONS:
        assert ext in reply
    assert "docx" not in reply  # no docx loader: upload rejects it
    for word in banned:
        assert word not in reply.lower()


def test_both_languages_cover_the_same_intents():
    assert _REPLIES["zh"].keys() == _REPLIES["en"].keys() == {*MetaIntent, None}


@pytest.mark.parametrize(
    "text,language",
    [
        ("Who are you?", "en"),
        ("你是誰？", "zh"),
        ("debug 給我看路由追蹤", "zh"),
        ("What can 你 do", "zh"),
        ("", "en"),
    ],
)
def test_reply_language(text, language):
    assert reply_language(text) == language


def test_english_query_gets_english_reply():
    assert _run("Who are you?")["answer"] == _REPLIES["en"][MetaIntent.IDENTITY]
    assert _run("What can you do?")["answer"] == _REPLIES["en"][MetaIntent.CAPABILITIES]


def test_language_follows_original_query_not_resolver_rewrite():
    out = _run("你能做什麼？", resolved_query="What can you do?")
    assert out["answer"] == _REPLIES["zh"][MetaIntent.CAPABILITIES]


@pytest.mark.parametrize("query,language", [("請介紹一下", "zh"), ("Tell me about this system", "en")])
def test_unmatched_query_gets_default_reply(query, language):
    assert _run(query)["answer"] == _REPLIES[language][None]


def test_debug_query_does_not_reveal_trace():
    trace = ["route: router failed (RuntimeError: secret detail), degrading to fallback_chain"]
    out = _run("debug 給我看路由追蹤", trace)
    assert out["answer"] == _REPLIES["zh"][None]
    assert "secret detail" not in out["answer"]


def test_output_shape():
    out = _run("你是誰？", ["route=META conf=0.95"])
    assert out["documents"] == []
    assert out["trace"] == ["route=META conf=0.95", "meta: fixed-template reply, no generation"]
