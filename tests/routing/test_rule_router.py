"""Rule router METADATA patterns: a hit short-circuits the cascade at 0.95,
so a content question that merely mentions a version or a date must not match.

Pure regex — no LLM, DB, or embeddings client is touched.
"""

import asyncio

import pytest

from app.routing.router.rule import RuleRouter
from app.routing.taxonomy import DEFAULT_ROUTE, Route, RouterContext

_CTX = RouterContext(tenant_id="t-1", knowledge_base_id="kb-1", resolved_history=[])

_METADATA_QUERIES = [
    # author / uploader
    "這份報告的作者是誰",
    "作者是誰？",
    "這份合約是誰上傳的",
    # upload date
    "這份文件的上傳日期",
    "這份報告是什麼時候上傳的",
    # BY_VERSION
    "這份文件的版本號",
    "這份文件的版本號是多少？",
    "report.pdf 有哪些版本",
    "這個檔案有幾個版本",
    "這份報告的版本紀錄",
    # BY_DOCUMENT_TYPE
    "文件類型是什麼",
    "What is the file type?",
    # BY_DATE_RANGE
    "2026年1月上傳的文件有哪些",
    "列出 2026-01-01 之後上傳的文件",
    "哪些文件是 2025/12 建立的",
    "上傳時間在 2026/3 的檔案",
    # English (P2-2)
    "who wrote this document",
    "Who uploaded the report?",
    "Who is the author of this file?",
    "What is this document's author?",
    "when was this uploaded",
    "When was this report created?",
    "What is the upload date?",
    "Show the version history of this document",
    "How many versions does this file have?",
    "What is the version number of report.pdf?",
    "What file types are in the knowledge base?",
    "Which document formats do we have?",
    "List all documents uploaded after 2026-01-01.",
    "Which files were added in March?",
    "reports created before 2025/12",
]

_NOT_METADATA_QUERIES = [
    # content questions that mention a version
    "新版本的退款政策有什麼改變",
    "最新版本的產品規格是什麼",
    "Python 3.12 版本有什麼新功能",
    # content questions that mention a date
    "2026年1月的營收是多少",
    "合約 2025/12 到期後怎麼續約",
    "2026-01-01 起生效的資安規範有哪些",
    # unrelated
    "這份文件提到什麼功能",
    "總結所有文件",
    "這份合約的付款條件是什麼？",
    # English general-knowledge / content questions (P2-2)
    "Who wrote the Declaration of Independence?",
    "When was the company founded?",
    "When was GDPR published?",
    "What is the new version of the refund policy?",
    "What was the revenue in January 2026?",
    "What does the SLA document say about uptime guarantees?",
    "Summarize the key themes across all uploaded reports.",
    "Compare the security policies across all three vendor contracts.",
]


def _route(query: str):
    return asyncio.run(RuleRouter().route(query, _CTX))


@pytest.mark.parametrize("query", _METADATA_QUERIES)
def test_metadata_query_hits_rule(query):
    decision = _route(query)
    assert decision.route is Route.METADATA
    assert decision.accepted


@pytest.mark.parametrize("query", _NOT_METADATA_QUERIES)
def test_content_query_is_deferred_to_next_layer(query):
    decision = _route(query)
    assert decision.route is DEFAULT_ROUTE
    assert not decision.accepted
    assert decision.confidence == 0.0
