"""三層 cascade：規則 -> embedding -> LLM，任一層回傳 `accepted=True` 即短路返回。

是否採用由每一層自己判斷（rule 命中、embedding 達門檻、LLM 成功回傳），cascade
不再拿 confidence 比門檻：LLM 層的 confidence 是 embedding 分數，比門檻會讓它
永遠被否決。實際上 LLM 層成功時一定被採用，它就是最後一層。

沒有任何一層 accepted 時保底走 `DEFAULT_ROUTE`（LOOKUP）——這是「信心不足」的降級，
在這裡直接處理即可，不需要繞去 `fallback_chain`：`fallback_chain.py`（M3）
處理的是另一種正交的情境——某一層呼叫本身失敗/丟例外（例如 Gemini API 逾時），
兩者刻意分開，避免這支檔案同時扛「信心判斷」與「故障復原」兩種邏輯。
"""

from __future__ import annotations

from app.routing.router.base import BaseRouter
from app.routing.router.embedding import EmbeddingRouter
from app.routing.router.llm import LLMRouter
from app.routing.router.rule import RuleRouter
from app.routing.taxonomy import DEFAULT_ROUTE, RouteDecision, RouterContext

ROUTER_VERSION = "cascade-v2"


class CascadeRouter(BaseRouter):
    router_version = ROUTER_VERSION

    def __init__(
        self,
        rule: BaseRouter | None = None,
        embedding: BaseRouter | None = None,
        llm: BaseRouter | None = None,
    ):
        self._layers: list[BaseRouter] = [
            rule or RuleRouter(),
            embedding or EmbeddingRouter(),
            llm or LLMRouter(),
        ]

    async def route(self, query: str, ctx: RouterContext) -> RouteDecision:
        last_decision: RouteDecision | None = None
        for layer in self._layers:
            decision = await layer.route(query, ctx)
            last_decision = decision
            if decision.accepted:
                return decision

        return RouteDecision(
            route=DEFAULT_ROUTE,
            confidence=last_decision.confidence if last_decision else 0.0,
            accepted=True,
            reasoning="cascade: 沒有任何一層 accepted，保底 LOOKUP",
        )
