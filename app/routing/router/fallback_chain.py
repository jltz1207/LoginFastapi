"""故障降級鏈：LLM router 失敗 -> embedding router -> 規則 router -> default(LOOKUP)。

這裡處理的是「呼叫本身丟例外」（API 逾時、額度用盡、網路錯誤…），與 `cascade.py`
處理的「信心不足」是正交的兩種情境，刻意分開（見 `cascade.py` docstring）。

第一個沒丟例外的層就是結果；若它沒有 `accepted`（例如 embedding 相似度不到門檻、
rule 沒命中），改回 `DEFAULT_ROUTE`。LLM 層成功時一律 accepted，直接採用。

每次降級都會被記錄下來，可從 `last_degradations` 讀出；呼叫方（未來 M5 的
`routing/graph.py`）負責把它併入 `RoutedAgentState.trace`。等 M10 落地
`ops/observability.py` 後，這裡的記錄方式會改成寫入正式的 observability schema。
"""

from __future__ import annotations

from app.routing.router.base import BaseRouter
from app.routing.router.embedding import EmbeddingRouter
from app.routing.router.llm import LLMRouter
from app.routing.router.rule import RuleRouter
from app.routing.taxonomy import DEFAULT_ROUTE, RouteDecision, RouterContext

ROUTER_VERSION = "fallback-chain-v2"


class FallbackChainRouter(BaseRouter):
    router_version = ROUTER_VERSION

    def __init__(
        self,
        llm: BaseRouter | None = None,
        embedding: BaseRouter | None = None,
        rule: BaseRouter | None = None,
    ):
        self._layers: list[tuple[str, BaseRouter]] = [
            ("llm", llm or LLMRouter()),
            ("embedding", embedding or EmbeddingRouter()),
            ("rule", rule or RuleRouter()),
        ]
        self.last_degradations: list[str] = []

    async def route(self, query: str, ctx: RouterContext) -> RouteDecision:
        self.last_degradations = []

        for name, layer in self._layers:
            try:
                decision = await layer.route(query, ctx)
            except Exception as exc:  # noqa: BLE001 - 降級鏈本來就要吞掉任何一層的失敗
                self.last_degradations.append(
                    f"{name} router failed ({type(exc).__name__}: {exc}), degrading to next layer"
                )
                continue

            if decision.accepted:
                return decision
            self.last_degradations.append(
                f"{name} router not accepted (route={decision.route.value} "
                f"conf={decision.confidence:.2f}), falling back to default LOOKUP"
            )
            return RouteDecision(
                route=DEFAULT_ROUTE,
                confidence=decision.confidence,
                accepted=True,
                reasoning="fallback_chain: " + "; ".join(self.last_degradations),
            )

        self.last_degradations.append("all layers failed, falling back to default LOOKUP")
        return RouteDecision(
            route=DEFAULT_ROUTE,
            confidence=0.0,
            accepted=True,
            reasoning="fallback_chain: " + "; ".join(self.last_degradations),
        )
