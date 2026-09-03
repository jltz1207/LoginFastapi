"""規則 fast path（<1ms，cascade 第一層）。

只攔截語意明確、不需要模型理解就能判斷的兩類查詢：
- META：問系統本身（"你能做什麼"、"你是誰"）。
- METADATA：查文件的結構化屬性（作者、日期、版本、檔案類型…）。

其餘一律回傳低信心（0.0）+ `DEFAULT_ROUTE`，讓 cascade 往下一層（embedding）走，
不在這裡猜測語意模糊的分類。
"""

from __future__ import annotations

import re

from app.routing.router.base import BaseRouter
from app.routing.taxonomy import DEFAULT_ROUTE, Route, RouteDecision, RouterContext

ROUTER_VERSION = "rule-v1"

_RULE_CONFIDENCE = 0.95

_META_PATTERNS = [
    re.compile(r"你(能|可以).{0,6}(做|提供|回答|協助)"),
    re.compile(r"你是(誰|什麼)"),
    re.compile(r"(這個)?系統.{0,6}(有|能).{0,6}功能"),
    re.compile(r"我可以問你什麼"),
    re.compile(r"what can you (do|help)", re.IGNORECASE),
    re.compile(r"who are you", re.IGNORECASE),
]

_METADATA_PATTERNS = [
    re.compile(r"作者是?誰"),
    re.compile(r"誰(寫|上傳|建立|修改)的"),
    re.compile(r"(上傳|發布|建立|修改)(日期|時間)"),
    re.compile(r"什麼時候(發布|上傳|建立)"),
    re.compile(r"版本(號)?"),
    re.compile(r"(檔案|文件)類型"),
    re.compile(r"file type", re.IGNORECASE),
    re.compile(r"\d{4}[-/年]\d{1,2}"),  # 日期格式，如 2026-01-01 / 2026年1月
]


class RuleRouter(BaseRouter):
    router_version = ROUTER_VERSION

    async def route(self, query: str, ctx: RouterContext) -> RouteDecision:
        if any(p.search(query) for p in _META_PATTERNS):
            return RouteDecision(
                route=Route.META,
                confidence=_RULE_CONFIDENCE,
                reasoning="rule: META pattern matched",
            )
        if any(p.search(query) for p in _METADATA_PATTERNS):
            return RouteDecision(
                route=Route.METADATA,
                confidence=_RULE_CONFIDENCE,
                reasoning="rule: METADATA pattern matched",
            )
        return RouteDecision(
            route=DEFAULT_ROUTE,
            confidence=0.0,
            reasoning="rule: no pattern matched, defer to next layer",
        )
