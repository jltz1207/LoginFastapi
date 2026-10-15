"""META 分支：系統性問題（"你能做什麼"類），固定模板回覆，**不即時生成**。

不回傳 `state.trace`：路由除錯走 LangSmith / log，不在聊天介面對使用者揭露內部紀錄。

修正職責接反問題：這個檔案先前放的是 `sql_query_node`（白名單參數化 SQL 查詢），
那屬於 METADATA 分支，已搬到 `routing/branches/metadata.py`。這裡改回它原本該做的
事——純字串模板 + 關鍵字判斷，完全不呼叫任何 LLM。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.routing.meta_intents import MetaIntent, classify_meta_intent

if TYPE_CHECKING:  # type-only: keeps `routing` free of a runtime import on `agent`
    from app.agent.state import RoutedAgentState

ROUTER_VERSION = "meta-v2"

_FIXED_REPLIES = {
    MetaIntent.CAPABILITIES: (
        "我是一個 RAG 查詢助理，可以幫你在知識庫裡查資料、做跨文件摘要、"
        "查詢文件的作者/日期等屬性、比較多份文件，或直接回答常識性問題。"
    ),
    MetaIntent.IDENTITY: "我是這個系統的查詢助理，負責把你的問題分派到最適合的處理路徑並回答。",
}
# embedding / LLM router 判成 META、但 `meta_intents` 的 pattern 沒命中時的回覆。
_DEFAULT_REPLY = "這是一個系統性問題。我目前只能針對知識庫內容或常識性問題作答，更多系統說明請參考文件。"


class MetaBranch:
    router_version = ROUTER_VERSION

    async def __call__(self, state: RoutedAgentState) -> dict:
        intent = classify_meta_intent(state.resolved_query)
        answer = _FIXED_REPLIES[intent] if intent is not None else _DEFAULT_REPLY

        return {
            "answer": answer,
            "documents": [],
            "trace": state.trace + ["meta: fixed-template reply, no generation"],
        }


meta_node = MetaBranch()
