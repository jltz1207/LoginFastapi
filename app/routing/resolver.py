"""Pre-router 對話歷史消歧（Query Resolver）。

`FOLLOW_UP` 不建 taxonomy 類別；改在這裡把可能依賴對話歷史的 query
（例如「那第二點呢？」）改寫成獨立完整問句。Router 因此得到一個不變式：
**永遠只收到完整問句**，不需要自己處理指代消解。

沒有對話歷史時直接回傳原始 query，不觸發任何 LLM 呼叫（多數 query 不是
追問，這條路徑要免費）。
"""

from __future__ import annotations

from typing import Protocol

from langchain_core.messages import BaseMessage

_REWRITE_SYSTEM_PROMPT = """\
你是對話歷史消歧器。給定對話歷史與使用者最新一句話，判斷這句話是否依賴前面的
對話才能理解（例如使用代名詞、省略主詞、承接前一個話題的追問）。

- 如果依賴，把它改寫成一句不需要對話歷史也能獨立理解的完整問句，語意必須與原意
  完全一致，不要新增原本沒有的資訊，也不要回答問題本身。
- 如果不依賴（本身已經是完整、獨立的問句），原樣輸出，不做任何修改。

只輸出改寫後的問句，不要加任何解釋或前綴。"""


class QueryRewriter(Protocol):
    """把 (query, history) 改寫成獨立完整問句的介面。

    `history` 直接收 langchain `BaseMessage`，跟 `AgentState.chat_history` 同一種
    型別，不再另外定義一個 role/content 的對話輪次模型。
    """

    async def rewrite(self, query: str, history: list[BaseMessage]) -> str: ...


class LLMQueryRewriter:
    """用 Gemini 做 query 改寫。`model` 可注入以利測試/替換模型。"""

    def __init__(self, model=None):
        self._model = model

    def _get_model(self):
        if self._model is None:
            from langchain_google_genai import ChatGoogleGenerativeAI

            self._model = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0)
        return self._model

    async def rewrite(self, query: str, history: list[BaseMessage]) -> str:
        # `msg.type` is langchain's role discriminator ("human"/"ai"/…), and
        # `msg.content` is str for ordinary turns but a list for multimodal ones.
        history_text = "\n".join(
            f"{msg.type}: {msg.content if isinstance(msg.content, str) else msg.content!s}"
            for msg in history
        )
        messages = [
            ("system", _REWRITE_SYSTEM_PROMPT),
            ("human", f"對話歷史：\n{history_text}\n\n最新一句話：{query}"),
        ]
        response = await self._get_model().ainvoke(messages)
        content = response.content
        return content.strip() if isinstance(content, str) else query


class QueryResolver:
    """對外入口。`resolve()` 保證輸出永遠是完整問句這個不變式。"""

    def __init__(self, rewriter: QueryRewriter | None = None):
        self._rewriter = rewriter or LLMQueryRewriter()

    async def resolve(
        self, query: str, history: list[BaseMessage] | None = None
    ) -> str:
        history = history or []
        if not history:
            return query

        resolved = await self._rewriter.rewrite(query, history)
        return resolved or query
