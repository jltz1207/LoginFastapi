"""LangGraph StateGraph 組裝：把 M1（resolver/taxonomy）、M2/M3（router cascade +
fallback chain）、M4（六個 leaf branch）接成一張完整的圖。

修正了先前 stub 版本的已知技術債（見 `progress.md` 現況盤點）：
- 移除已刪除的 `OUT_OF_SCOPE`/`refuse`，改成完整的 6 類對照表（含 `META`/`NO_RETRIEVAL`）。
- `resolve_query`/`route_query` 從 `pass` stub 改成真正接上 M1/M2/M3 的實作。
- `RAGState["trace"]` 由 `initial_state()` 統一給預設值 `[]`，不會是 undefined。
- `checkpointer` 不再是未定義的裸名字：`AsyncSqliteSaver` 是 async context manager，
  不能在模組載入時就同步建好，所以改成 `graph_app()` 這個 async context manager；
  測試/一次性呼叫可以用 `compile_app()`（不落地存檔）。
- import 改用 M4 修正後的正確名字：`metadata_query_node`（原本錯接成 `sql_query_node`
  from `meta.py`）、`global_summary_node`（原本 `lobal_summary_node` typo）。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TypedDict

from app.agent.graphs.base import BaseGraphFactory
from langgraph.graph.state import CompiledStateGraph
from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.base import BaseCheckpointSaver

from app.agent.graphs.lookup_rag import LookupRagGraphFactory
from app.routing.branches.global_summary import global_summary_node

from app.routing.branches.meta import meta_node
from app.routing.branches.metadata import metadata_query_node
from app.routing.branches.multi_hop import agentic_subgraph
from app.routing.branches.no_retrieval import no_retrieval_node
from app.routing.resolver import ConversationTurn, QueryResolver
from app.routing.router.base import BaseRouter
from app.routing.router.cascade import CascadeRouter
from app.routing.router.fallback_chain import FallbackChainRouter
from app.routing.taxonomy import CONFIDENCE_THRESHOLD, DEFAULT_ROUTE, Route, RouterContext


class RAGState(TypedDict):
    query: str
    resolved_query: str
    conversation_history: list[ConversationTurn]
    tenant_id: str
    knowledge_base_id: str
    route: str
    confidence: float
    filters: dict
    documents: list
    answer: str
    trace: list[str]


def initial_state(
    query: str,
    knowledge_base_id: str,
    tenant_id: str,
    conversation_history: list[ConversationTurn] | None = None,
) -> RAGState:
    """建構一個乾淨的初始 state。`trace` 一律從 `[]` 開始。"""
    return {
        "query": query,
        "resolved_query": "",
        "conversation_history": conversation_history or [],
        "tenant_id": tenant_id,
        "knowledge_base_id": knowledge_base_id,
        "route": "",
        "confidence": 0.0,
        "filters": {},
        "documents": [],
        "answer": "",
        "trace": [],
    }


class ResolverNode:
    """`resolve` 節點：接上 M1 的 `QueryResolver`，保證 `resolved_query` 永遠是完整問句。"""

    def __init__(self, resolver: QueryResolver | None = None):
        self._resolver = resolver or QueryResolver()

    async def __call__(self, state: RAGState) -> dict:
        resolved = await self._resolver.resolve(state["query"], state.get("conversation_history"))
        return {
            "resolved_query": resolved,
            "trace": state.get("trace", []) + ["resolve: query resolved to standalone question"],
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
        self._router = router or CascadeRouter()
        self._fallback = fallback or FallbackChainRouter()

    async def __call__(self, state: RAGState) -> dict:
        ctx = RouterContext(
            tenant_id=state["tenant_id"],
            knowledge_base_id=state["knowledge_base_id"],
            resolved_history=[turn.content for turn in state.get("conversation_history", [])],
        )
        trace = list(state.get("trace", []))

        try:
            decision = await self._router.route(state["resolved_query"], ctx)
        except Exception as exc:  # noqa: BLE001 - 呼叫失敗要降級，不能讓整個 graph 掛掉
            trace.append(f"route: router failed ({type(exc).__name__}: {exc}), degrading to fallback_chain")
            decision = await self._fallback.route(state["resolved_query"], ctx)
            trace.extend(
                f"fallback_chain: {entry}" for entry in getattr(self._fallback, "last_degradations", [])
            )

        trace.append(f"route={decision.route.value} conf={decision.confidence:.2f}")
        return {
            "route": decision.route.value,
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


def select_branch(state: RAGState) -> str:
    """confidence < 門檻一律保底走 LOOKUP，不管 router 實際輸出什麼 route。"""
    if state["confidence"] < CONFIDENCE_THRESHOLD:
        return _ROUTE_TO_NODE[DEFAULT_ROUTE.value]
    return _ROUTE_TO_NODE[state["route"]]

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
        g = StateGraph(RAGState)
        g.add_node("resolve", ResolverNode(resolver=resolver))
        g.add_node("route", RouterNode(router=router, fallback=fallback))
        search_graph = LookupRagGraphFactory.build(checkpointer=checkpointer)
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


# def compile_app(*, checkpointer=None, **build_graph_kwargs):
#     """不需要落地存檔時用這個（測試、一次性呼叫）。`checkpointer=None` 就是不持久化。"""
#     return build_graph(**build_graph_kwargs).compile(checkpointer=checkpointer)


# @asynccontextmanager
# async def graph_app(db_path: str = "checkpoints.sqlite", **build_graph_kwargs):
#     """生產用法：`async with graph_app() as app: await app.ainvoke(state, config=...)`。

#     `AsyncSqliteSaver.from_conn_string()` 本身是 async context manager，沒辦法在
#     模組載入時就同步組出一個裸的 `checkpointer` 變數（這正是舊版 stub 的 bug）。
#     """
#     from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

#     async with AsyncSqliteSaver.from_conn_string(db_path) as checkpointer:
#         yield build_graph(**build_graph_kwargs).compile(checkpointer=checkpointer)
