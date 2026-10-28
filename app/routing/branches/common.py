"""所有 leaf branch 共用的型別與租戶隔離守門函式。

租戶隔離規則：leaf node 必須自行強制注入 `knowledge_base_id`，
不得信任 router 輸出的 `filters`。`enforce_knowledge_base_id()` 是這個規則
的唯一實作，所有會碰知識庫/資料庫的分支都必須先呼叫它，而不是自己去
`state.filters` 或其他來源東拼西湊 tenant 邊界。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# Chunk is re-exported: global_summary.py and agent/nodes/retrieval.py import it from here.
from app.agent.state import Chunk, RoutedAgentState

__all__ = ["Chunk", "FINAL_ANSWER_TAG", "enforce_knowledge_base_id", "format_citations"]

# 產生「使用者看到的最終答案」的那一次 LLM 呼叫要帶這個 tag（`ainvoke(..., config={"tags": [...]})`）。
# 同一個 node 裡可能還有其他 LLM 呼叫（MULTI_HOP 的 decompose、GLOBAL 的 MAP），
# `POST /chat` 的 SSE 只串流帶這個 tag 的 token。
FINAL_ANSWER_TAG = "final_answer"


def enforce_knowledge_base_id(state: RoutedAgentState) -> str:
    """從 state 強制取出 `knowledge_base_id`，忽略 `state.filters`。

    找不到就直接拋錯——這是租戶邊界，寧可 fail fast 也不能悄悄放行成
    跨租戶查詢。`state.filters` 來自 router（機率性元件），任何情況下
    都不能被當作租戶邊界依據，即使它剛好也帶了 `knowledge_base_id` 這個 key。
    """
    knowledge_base_id = state.knowledge_base_id
    if not knowledge_base_id:
        raise ValueError(
            "knowledge_base_id missing from state; refusing to query without tenant scope"
        )
    return knowledge_base_id


def format_citations(chunks: list[Chunk]) -> str:
    if not chunks:
        return ""
    lines = [f"[{i + 1}] {c.source or c.chunk_id}" for i, c in enumerate(chunks)]
    return "\n".join(lines)
