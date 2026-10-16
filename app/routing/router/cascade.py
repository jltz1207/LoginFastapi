"""三層 cascade：規則 -> embedding -> LLM，任一層回傳 `accepted=True` 即短路返回。

是否採用由每一層自己判斷（rule 命中、embedding 達門檻、LLM 成功回傳），cascade
不再拿 confidence 比門檻：LLM 層的 confidence 是 embedding 分數，比門檻會讓它
永遠被否決。實際上 LLM 層成功時一定被採用，它就是最後一層。

沒有任何一層 accepted 時保底走 `DEFAULT_ROUTE`（LOOKUP）——這是「信心不足」的降級。

「故障復原」原則上仍交給 `fallback_chain.py`（M3）：rule / embedding 層丟例外時，
cascade 照樣往外丟，由 `RouterNode` 改走 fallback chain。唯一的例外是 LLM 層：
走到 LLM 層時 rule / embedding 都已經跑完且沒有 accepted，fallback chain 重跑一次
只會得到同樣的結果（LLM 再失敗一次，embedding 不到門檻 → LOOKUP），所以 LLM 層
丟例外時 cascade 直接保底 LOOKUP，不浪費前面已經跑過的結果。
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.routing.router.base import BaseRouter
from app.routing.router.embedding import EmbeddingRouter
from app.routing.router.llm import LLMRouter
from app.routing.router.rule import RuleRouter
from app.routing.taxonomy import DEFAULT_ROUTE, RouteDecision, RouterContext

ROUTER_VERSION = "cascade-v3"

logger = get_logger(__name__)


class CascadeRouter(BaseRouter):
    router_version = ROUTER_VERSION

    def __init__(
        self,
        rule: BaseRouter | None = None,
        embedding: BaseRouter | None = None,
        llm: BaseRouter | None = None,
    ):
        self._fast_layers: list[BaseRouter] = [
            rule or RuleRouter(),
            embedding or EmbeddingRouter(),
        ]
        self._llm = llm or LLMRouter()

    async def route(self, query: str, ctx: RouterContext) -> RouteDecision:
        last_decision: RouteDecision | None = None
        # rule / embedding 的例外刻意不攔，交給 RouterNode -> fallback chain
        for layer in self._fast_layers:
            decision = await layer.route(query, ctx)
            last_decision = decision
            if decision.accepted:
                return decision

        last_confidence = last_decision.confidence if last_decision else 0.0
        try:
            decision = await self._llm.route(query, ctx)
        except Exception as exc:  # noqa: BLE001 - LLM 失敗時 fallback chain 只會重跑出同樣的 LOOKUP
            logger.warning("cascade llm layer failed, falling back to default route", exc_info=True)
            return RouteDecision(
                route=DEFAULT_ROUTE,
                confidence=last_confidence,
                accepted=True,
                reasoning=f"cascade: llm layer failed ({type(exc).__name__}), 保底 LOOKUP",
            )
        if decision.accepted:
            return decision

        return RouteDecision(
            route=DEFAULT_ROUTE,
            confidence=decision.confidence,
            accepted=True,
            reasoning="cascade: 沒有任何一層 accepted，保底 LOOKUP",
        )
