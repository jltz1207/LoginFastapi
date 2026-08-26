"""投機並行（Speculative Execution）：把 router 決策時間藏在檢索延遲後面。

不等 router 決策完成，先對最可能的路徑（`DEFAULT_ROUTE`，即 `LOOKUP`）發起檢索；
router 判定結果與投機路徑一致就直接沿用投機結果，不同就**確實丟棄**投機結果、
不得回傳給下游，改用 router 真正判定的路徑重新執行一次。

跟 `cascade.py`（信心不足降級）、`fallback_chain.py`（呼叫失敗降級）一樣，這裡
處理的是第三種正交的關注點：**延遲優化**，不影響路由正確性——最終回傳的結果
永遠對應「router 真正判定的路徑」，投機執行只是用來省時間，不會讓答案跟著
投機路徑跑掉（開發約定：不得因為「已經跑出來了」就將就使用不同路徑的結果）。

還沒接進 `routing/graph.py` 的 `StateGraph`（那需要把 `RouterNode`/`select_branch`
的節點結構整個改掉，是更大的架構調整，超出這個元件本身的範圍）。這裡先把元件
本身做完整、做對、做好測試；未來要接的話，`dispatch` 參數可以用
`graph._ROUTE_TO_NODE` 對照表 + 對應的 leaf node 組出來。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from routing.router.base import BaseRouter
from routing.taxonomy import CONFIDENCE_THRESHOLD, DEFAULT_ROUTE, Route, RouteDecision, RouterContext

ROUTER_VERSION = "speculative-v1"

Dispatch = Callable[[str, dict], Awaitable[dict]]


@dataclass
class SpeculativeResult:
    decision: RouteDecision
    branch_result: dict
    speculation_hit: bool
    """True：router 判定的路徑跟投機路徑一致，沿用了投機結果。
    False：投機結果已被丟棄，`branch_result` 是重新執行『router 真正判定路徑』的結果。"""
    router_latency_seconds: float
    speculative_latency_seconds: float
    total_latency_seconds: float

    @property
    def trace_entry(self) -> str:
        return (
            f"speculative: hit={self.speculation_hit} "
            f"router={self.router_latency_seconds:.3f}s "
            f"speculative={self.speculative_latency_seconds:.3f}s "
            f"total={self.total_latency_seconds:.3f}s"
        )


class SpeculativeExecutor:
    router_version = ROUTER_VERSION

    def __init__(
        self,
        router: BaseRouter,
        dispatch: Dispatch,
        speculative_route: Route = DEFAULT_ROUTE,
        threshold: float = CONFIDENCE_THRESHOLD,
        clock=time.monotonic,
    ):
        """`router`：實際的 router（例如 `CascadeRouter`）。
        `dispatch`：`(route_value, state) -> dict`，執行指定路徑對應的 leaf branch。
        投機執行跟丟棄後的重跑都透過同一個 `dispatch` 呼叫，確保兩條路徑跑的是
        同一份程式碼，不會出現「投機路徑」和「正式路徑」邏輯分岔的風險。
        """
        self._router = router
        self._dispatch = dispatch
        self._speculative_route = speculative_route
        self._threshold = threshold
        self._clock = clock

    async def run(self, query: str, ctx: RouterContext, state: dict) -> SpeculativeResult:
        start = self._clock()

        router_task = asyncio.create_task(self._timed(self._router.route(query, ctx)))
        speculative_task = asyncio.create_task(
            self._timed(self._dispatch(self._speculative_route.value, state))
        )

        (decision, router_latency), (speculative_result, speculative_latency) = await asyncio.gather(
            router_task, speculative_task
        )

        actual_route = decision.route if decision.confidence >= self._threshold else DEFAULT_ROUTE

        if actual_route == self._speculative_route:
            branch_result = speculative_result
            speculation_hit = True
        else:
            # 投機結果的 route 跟真正判定的不一樣：確實丟棄，絕不回傳給下游。
            branch_result = await self._dispatch(actual_route.value, state)
            speculation_hit = False

        return SpeculativeResult(
            decision=decision,
            branch_result=branch_result,
            speculation_hit=speculation_hit,
            router_latency_seconds=router_latency,
            speculative_latency_seconds=speculative_latency,
            total_latency_seconds=self._clock() - start,
        )

    async def _timed(self, coro):
        start = self._clock()
        result = await coro
        return result, self._clock() - start
