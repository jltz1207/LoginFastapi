"""LangGraph StateGraph 組裝：把 M1（resolver/taxonomy）、M2/M3（router cascade +
fallback chain）、M4（六個 leaf branch）接成一張完整的圖。

    START → resolve → route ─┬→ lookup / global / metadata / multi_hop / no_retrieval / meta → END
                             └ router 沒有 accepted 的決策一律改走 lookup（RouterNode）

`RoutedGraphFactory.build()` 的每個節點都可注入假物件，測試不需要真的打 Gemini/Chroma/PostgreSQL。
"""

from __future__ import annotations

from app.agent.graphs.base import BaseGraphFactory
from langgraph.graph.state import CompiledStateGraph
from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.base import BaseCheckpointSaver

from app.agent.graphs.lookup_rag import LookupRagGraphFactory
from app.agent.state import RoutedAgentState
from app.core.logging import get_logger
from app.routing.branches.global_summary import global_summary_node

from app.routing.branches.meta import meta_node
from app.routing.branches.metadata import metadata_query_node
from app.routing.branches.multi_hop import agentic_subgraph
from app.routing.branches.no_retrieval import no_retrieval_node
from langchain_core.messages import BaseMessage

from app.routing.resolver import QueryResolver
from app.routing.router.base import BaseRouter
from app.routing.router.defaults import get_default_fallback, get_default_router
from app.routing.taxonomy import DEFAULT_ROUTE, Route, RouterContext

logger = get_logger(__name__)

_DIALOGUE_TYPES = ("human", "ai")

# resolver / router 只看最近幾則訊息：checkpointer 會累積整個 thread 的歷史，
# 全部送進 prompt 會讓成本與延遲隨對話長度線性成長。指代消解只需要最近幾輪。
_PRIOR_TURNS_LIMIT = 6


def _dialogue_turns(chat_history: list[BaseMessage]) -> list[BaseMessage]:
    """只保留使用者/助理的對話輪次，餵給 resolver 與 router。

    `chat_history` 是全圖共用的訊息串，lookup 子圖執行 tool 之後產生的
    `ToolMessage`（內含 web search 結果等檢索內容）也會流進來。
    `RouterContext.resolved_history` 明文禁止夾帶檢索到的文件內容——避免文件
    內容夾帶 prompt injection 影響分類結果——所以這裡一律濾掉 tool/system 訊息。
    """
    return [msg for msg in chat_history if msg.type in _DIALOGUE_TYPES]


def _prior_turns(chat_history: list[BaseMessage]) -> list[BaseMessage]:
    """這一輪之前、最近 `_PRIOR_TURNS_LIMIT` 則對話訊息。

    API 建 input 時會把這一輪的問題當成 `HumanMessage` 放進 `chat_history`，
    所以最後一則就是 `state.query` 本身。把它當成 history 會讓 resolver 永遠
    以為有對話歷史（第一輪也呼叫 rewrite LLM），而且同一句話在 prompt 裡出現兩次。
    """
    turns = _dialogue_turns(chat_history)
    if turns and turns[-1].type == "human":
        turns = turns[:-1]
    return turns[-_PRIOR_TURNS_LIMIT:]


def initial_state(
    query: str,
    knowledge_base_id: str,
    tenant_id: str,
    user_id: str,
    chat_history: list[BaseMessage] | None = None,
) -> RoutedAgentState:
    """建構一個乾淨的初始 state。

    其餘欄位（`resolved_query`/`route`/`confidence`/`filters`/`documents`/
    `answer`/`trace`）都由 `RoutedAgentState` 的欄位預設值提供，`trace` 一樣
    保證從 `[]` 開始，不會是 undefined。
    """
    return RoutedAgentState(
        query=query,
        knowledge_base_id=knowledge_base_id,
        tenant_id=tenant_id,
        user_id=user_id,
        chat_history=chat_history or [],
    )


class ResolverNode:
    """`resolve` 節點：接上 M1 的 `QueryResolver`，保證 `resolved_query` 永遠是完整問句。

    resolver 只是最佳化：rewrite 失敗時降級成原始 query，不讓整張 graph 失敗。
    """

    def __init__(self, resolver: QueryResolver | None = None):
        self._resolver = resolver or QueryResolver()

    async def __call__(self, state: RoutedAgentState) -> dict:
        try:
            resolved = await self._resolver.resolve(state.query, _prior_turns(state.chat_history))
            entry = "resolve: query resolved to standalone question"
        except Exception as exc:  # noqa: BLE001 - 降級成原始 query；CancelledError 等 BaseException 照樣往上丟
            logger.warning("routed_rag.resolver_failed", error_type=type(exc).__name__, exc_info=True)
            resolved = state.query
            entry = f"resolve: rewriter failed ({type(exc).__name__}), using original query"
        return {
            "resolved_query": resolved,
            "trace": state.trace + [entry],
        }


class RouterNode:
    """`route` 節點：主線走 `router`（預設 `CascadeRouter`，三層信心遞增短路）；
    router 本身丟例外時（呼叫失敗，不是信心不足）才降級走 `fallback`
    （預設 `FallbackChainRouter`）。兩種降級路徑分開處理，呼應 `cascade.py`/
    `fallback_chain.py` 的職責劃分。

    傳入 `NoOpRouter()` 當 `router` 參數即可讓「關 router」走同一段程式碼路徑
    （ablation 實驗用），不需要另外的 if/else 分支。
    """

    def __init__(self, router: BaseRouter | None = None, fallback: BaseRouter | None = None):
        # 預設用 process 共用的 router：graph 每次請求重建，new 新 router 會讓範例向量快取失效
        self._router = router or get_default_router()
        self._fallback = fallback or get_default_fallback()

    async def __call__(self, state: RoutedAgentState) -> dict:
        ctx = RouterContext(
            tenant_id=state.tenant_id,
            knowledge_base_id=state.knowledge_base_id,
            resolved_history=[
                msg.content if isinstance(msg.content, str) else str(msg.content)
                for msg in _prior_turns(state.chat_history)
            ],
        )
        trace = list(state.trace)

        try:
            decision = await self._router.route(state.resolved_query, ctx)
        except Exception as exc:  # noqa: BLE001 - 呼叫失敗要降級，不能讓整個 graph 掛掉
            trace.append(f"route: router failed ({type(exc).__name__}: {exc}), degrading to fallback_chain")
            decision = await self._fallback.route(state.resolved_query, ctx)
        trace.extend(decision.degradations)

        route = decision.route
        if not decision.accepted:
            # 是否採用由 router 判斷（`RouteDecision.accepted`），這裡不再拿 confidence 比門檻；
            # 只有直接注入單一層 router（例如 `EmbeddingRouter`）時才會走到這裡。
            trace.append(f"route: {route.value} not accepted, falling back to {DEFAULT_ROUTE.value}")
            route = DEFAULT_ROUTE

        trace.append(f"route={route.value} conf={decision.confidence:.2f}")
        return {
            "route": route.value,
            "confidence": decision.confidence,
            "filters": decision.filters,
            "trace": trace,
        }


_ROUTE_TO_NODE: dict[str, str] = {
    Route.LOOKUP.value: "lookup",
    Route.GLOBAL.value: "global",
    Route.METADATA.value: "metadata",
    Route.MULTI_HOP.value: "multi_hop",
    Route.NO_RETRIEVAL.value: "no_retrieval",
    Route.META.value: "meta",
}


def select_branch(state: RoutedAgentState) -> str:
    """依 `RouterNode` 寫入的 route 分派；尚未 routing（`route=""`）時保底走 LOOKUP。

    不再比 confidence：是否採用已由 router 判斷，這裡再比門檻會把 LLM 層的決定否決掉。
    """
    return _ROUTE_TO_NODE.get(state.route, _ROUTE_TO_NODE[DEFAULT_ROUTE.value])

class RoutedGraphFactory(BaseGraphFactory):
    @staticmethod
    def build(
        checkpointer: BaseCheckpointSaver,
        *,
        router: BaseRouter | None = None,
        fallback: BaseRouter | None = None,
        resolver: QueryResolver | None = None,
        lookup=None,
        global_summary=None,
        metadata=None,
        multi_hop=None,
        no_retrieval=None,
        meta=None,
    ) -> CompiledStateGraph:
        """組好但還沒 compile 的 graph。每個節點都可覆寫，方便測試注入假物件，
        不需要真的打 Gemini/Chroma/PostgreSQL。留空一律用 M1~M4 的預設實作。
        """
        g = StateGraph(RoutedAgentState)
        g.add_node("resolve", ResolverNode(resolver=resolver))
        g.add_node("route", RouterNode(router=router, fallback=fallback))
        search_graph = LookupRagGraphFactory.build(checkpointer=checkpointer, tools=None)
        g.add_node("lookup", lookup or search_graph)
        g.add_node("global", global_summary or global_summary_node)
        g.add_node("metadata", metadata or metadata_query_node)
        g.add_node("multi_hop", multi_hop or agentic_subgraph)
        g.add_node("no_retrieval", no_retrieval or no_retrieval_node)
        g.add_node("meta", meta or meta_node)

        g.add_edge(START, "resolve")
        g.add_edge("resolve", "route")
        g.add_conditional_edges("route", select_branch)
        for leaf in ("lookup", "global", "metadata", "multi_hop", "no_retrieval", "meta"):
            g.add_edge(leaf, END)

        return g.compile()
