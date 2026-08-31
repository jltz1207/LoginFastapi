from abc import ABC, abstractmethod

from app.routing.taxonomy import DEFAULT_ROUTE, RouteDecision, RouterContext


class BaseRouter(ABC):
    """所有 router 層（rule/embedding/LLM/cascade/NoOp）共用的介面。"""

    router_version: str = "unversioned"

    @abstractmethod
    async def route(self, query: str, ctx: RouterContext) -> RouteDecision:
        pass


class NoOpRouter(BaseRouter):
    """關閉 router 時使用，讓「開 router」／「關 router」走同一段程式碼路徑，
    呼叫端不需要用 `if router is not None` 分岔（那樣會污染 ablation 實驗）。

    永遠回傳 `DEFAULT_ROUTE`（LOOKUP）。`confidence=1.0` 不代表任何信心語意，
    只是滿足 `RouteDecision` 的 range 驗證；下游不應該讀這個值做任何判斷。
    """

    router_version = "noop-v1"

    async def route(self, query: str, ctx: RouterContext) -> RouteDecision:
        return RouteDecision(
            route=DEFAULT_ROUTE,
            confidence=1.0,
            reasoning="NoOpRouter: router disabled, always LOOKUP",
        )