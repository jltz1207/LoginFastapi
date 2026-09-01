"""META 分支：系統性問題（"你能做什麼"類），固定模板回覆或回傳 trace，**不即時生成**。

修正職責接反問題：這個檔案先前放的是 `sql_query_node`（白名單參數化 SQL 查詢），
那屬於 METADATA 分支，已搬到 `routing/branches/metadata.py`。這裡改回它原本該做的
事——純字串模板 + 關鍵字判斷，完全不呼叫任何 LLM。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # type-only: keeps `routing` free of a runtime import on `agent`
    from app.agent.state import RoutedAgentState

ROUTER_VERSION = "meta-v1"

_FIXED_REPLIES = {
    "capabilities": (
        "我是一個 RAG 查詢助理，可以幫你在知識庫裡查資料、做跨文件摘要、"
        "查詢文件的作者/日期等屬性、比較多份文件，或直接回答常識性問題。"
    ),
    "identity": "我是這個系統的查詢助理，負責把你的問題分派到最適合的處理路徑並回答。",
    "default": "這是一個系統性問題。我目前只能針對知識庫內容或常識性問題作答，更多系統說明請參考文件。",
}

_TRACE_KEYWORDS = ["trace", "追蹤", "路由紀錄", "debug"]
_IDENTITY_KEYWORDS = ["你是誰", "who are you"]
_CAPABILITY_KEYWORDS = ["你能做什麼", "你可以做什麼", "什麼功能", "what can you"]


def _format_trace(trace: list[str]) -> str:
    if not trace:
        return "目前沒有可顯示的路由追蹤紀錄。"
    return "路由追蹤：\n" + "\n".join(f"- {entry}" for entry in trace)


def _matches(query: str, query_lower: str, keywords: list[str]) -> bool:
    return any(kw in query or kw in query_lower for kw in keywords)


class MetaBranch:
    router_version = ROUTER_VERSION

    async def __call__(self, state: RoutedAgentState) -> dict:
        query = state.resolved_query
        query_lower = query.lower()

        if _matches(query, query_lower, _TRACE_KEYWORDS):
            answer = _format_trace(state.trace)
        elif _matches(query, query_lower, _IDENTITY_KEYWORDS):
            answer = _FIXED_REPLIES["identity"]
        elif _matches(query, query_lower, _CAPABILITY_KEYWORDS):
            answer = _FIXED_REPLIES["capabilities"]
        else:
            answer = _FIXED_REPLIES["default"]

        return {
            "answer": answer,
            "documents": [],
            "trace": state.trace + ["meta: fixed-template reply, no generation"],
        }


meta_node = MetaBranch()
