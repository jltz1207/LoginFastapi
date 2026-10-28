"""META 分支：系統性問題（"你能做什麼"類），固定模板回覆，**不即時生成**。

用 `meta_intents.classify_meta_intent()`（與 rule router 共用）選模板，依使用者原文選中 / 英文，
完全不呼叫任何 LLM，也不碰知識庫或資料庫。

不回傳 `state.trace`：路由除錯走 LangSmith / log，不在聊天介面對使用者揭露內部紀錄。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal

from app.routing.meta_intents import MetaIntent, classify_meta_intent

if TYPE_CHECKING:  # type-only: keeps `routing` free of a runtime import on `agent`
    from app.agent.state import RoutedAgentState

ROUTER_VERSION = "meta-v6"

Language = Literal["zh", "en"]

_CJK = re.compile(r"[㐀-鿿豈-﫿]")

# key `None`：embedding / LLM router 判成 META、但 `meta_intents` 的 pattern 沒命中時的回覆。
# CAPABILITIES 只列目前真的能用的功能；新增/移除 route 或 METADATA query type 時兩種語言都要同步更新。
# 每行對應：LOOKUP / GLOBAL / MULTI_HOP / METADATA（`metadata._ALLOWED_FILE_EXTENSIONS`）/ NO_RETRIEVAL。
# GLOBAL 依賴 ingestion 產生的 `Document.summary`（`app/ingestion/summarizer.py`）。
_REPLIES: dict[Language, dict[MetaIntent | None, str]] = {
    "zh": {
        MetaIntent.CAPABILITIES: (
            "我是這個知識庫的查詢助理，可以：\n"
            "- 回答知識庫文件裡的具體內容\n"
            "- 摘要整個知識庫，或歸納多份文件的共同主題\n"
            "- 比較多份文件，或回答需要串連多處內容的問題\n"
            "- 依上傳日期、檔案類型（pdf / md），或同一檔名的歷次上傳版本查文件\n"
            "- 直接回答不需要查知識庫的常識性問題"
        ),
        MetaIntent.IDENTITY: "我是這個系統的查詢助理，負責把你的問題分派到最適合的處理路徑並回答。",
        None: "這是一個關於系統本身的問題。我可以回答知識庫內容或常識性問題；想知道支援哪些功能，可以問「你能做什麼？」。",
    },
    "en": {
        MetaIntent.CAPABILITIES: (
            "I'm the query assistant for this knowledge base. I can:\n"
            "- Answer questions about the content of your documents\n"
            "- Summarize the whole knowledge base, or find the common themes across documents\n"
            "- Compare documents, or answer questions that connect information across several places\n"
            "- Find documents by upload date, file type (pdf / md), or the upload history of a file name\n"
            "- Answer general-knowledge questions that don't need the knowledge base"
        ),
        MetaIntent.IDENTITY: (
            "I'm this system's query assistant. I route your question to the best way of handling it and answer it."
        ),
        None: (
            "This is a question about the system itself. I can answer questions about your knowledge base "
            'or general-knowledge questions; ask "What can you do?" to see what I support.'
        ),
    },
}


def reply_language(text: str) -> Language:
    """Any CJK character → zh; otherwise en. Mixed queries ("debug 給我看路由追蹤") count as zh."""
    return "zh" if _CJK.search(text) else "en"


class MetaBranch:
    router_version = ROUTER_VERSION

    async def __call__(self, state: RoutedAgentState) -> dict:
        intent = classify_meta_intent(state.resolved_query)
        # Language follows what the user typed, not the resolver's rewrite.
        language = reply_language(state.query or state.resolved_query)
        answer = _REPLIES[language][intent]

        return {
            "answer": answer,
            "documents": [],
            "trace": state.trace + ["meta: fixed-template reply, no generation"],
        }


meta_node = MetaBranch()
