"""規則 fast path（<1ms，cascade 第一層）。

只攔截語意明確、不需要模型理解就能判斷的兩類查詢：
- META：問系統本身（"你能做什麼"、"你是誰"）。
- METADATA：查文件的結構化屬性（作者、日期、版本、檔案類型…）。

其餘一律回傳低信心（0.0）+ `DEFAULT_ROUTE`，讓 cascade 往下一層（embedding）走，
不在這裡猜測語意模糊的分類。
"""

from __future__ import annotations

import re

from app.routing.meta_intents import classify_meta_intent
from app.routing.router.base import BaseRouter
from app.routing.taxonomy import DEFAULT_ROUTE, Route, RouteDecision, RouterContext

ROUTER_VERSION = "rule-v4"

_RULE_CONFIDENCE = 0.95

# META patterns live in `routing/meta_intents.py`, shared with the META branch.

# 命中就以 0.95 短路 cascade，所以「版本」與日期都必須錨定：只出現「版本」或某個日期的
# 內容問題（「新版本的退款政策有什麼改變」、「2026年1月的營收是多少」）要交給下一層。
_DOCUMENT_REF = r"(文件|檔案|這份|此份|該份|報告|\.(pdf|md|docx)\b)"
_UPLOAD_ACTION = r"(上傳|發布|發佈|建立|新增)"
_DATE = r"\d{4}[-/年]\d{1,2}"  # 2026-01-01 / 2026/1 / 2026年1月

_METADATA_PATTERNS = [
    re.compile(r"作者是?誰"),
    re.compile(r"誰(寫|上傳|建立|修改)的"),
    re.compile(r"(上傳|發布|建立|修改)(日期|時間)"),
    re.compile(r"什麼時候(發布|上傳|建立)"),
    # BY_VERSION：「版本」指同一檔名的上傳紀錄，必須指向文件本身
    re.compile(_DOCUMENT_REF + r".{0,8}(版本號|版號|版本紀錄|版本歷史|(幾|哪些)個?版本)", re.IGNORECASE),
    re.compile(r"(檔案|文件)類型"),
    re.compile(r"file type", re.IGNORECASE),
    # BY_DATE_RANGE：日期必須和上傳/建立這類動作一起出現，前後順序都可以
    re.compile(_DATE + r".{0,12}" + _UPLOAD_ACTION),
    re.compile(_UPLOAD_ACTION + r".{0,12}" + _DATE),
]


class RuleRouter(BaseRouter):
    router_version = ROUTER_VERSION

    async def route(self, query: str, ctx: RouterContext) -> RouteDecision:
        if classify_meta_intent(query) is not None:
            return RouteDecision(
                route=Route.META,
                confidence=_RULE_CONFIDENCE,
                accepted=True,
                reasoning="rule: META pattern matched",
            )
        if any(p.search(query) for p in _METADATA_PATTERNS):
            return RouteDecision(
                route=Route.METADATA,
                confidence=_RULE_CONFIDENCE,
                accepted=True,
                reasoning="rule: METADATA pattern matched",
            )
        return RouteDecision(
            route=DEFAULT_ROUTE,
            confidence=0.0,
            reasoning="rule: no pattern matched, defer to next layer",
        )
