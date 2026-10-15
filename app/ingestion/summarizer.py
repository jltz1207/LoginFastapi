"""Document summary：把 `documents.raw_text` 壓成一段 summary，給 GLOBAL 分支讀。

長文件放不進一次 LLM call，所以走 map-reduce：

    raw_text ─split─► sections ─map（並行）─► partial summaries
                                              │ 合起來還是太長 → 分批再 map 一輪
                                              ▼
                                           reduce ─► documents.summary

短文件（只有一個 section）直接 reduce，只花一次 call。
設計背景見 docs/route/global/document-summary-storage-20261007.md。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.core.config import settings
from app.llm.factory import LLMFactory
from app.rag.prompts import DOCUMENT_SUMMARY_MAP_PROMPT, DOCUMENT_SUMMARY_REDUCE_PROMPT

# Summary length scales with document size (design doc mitigation #1), so a 500-page
# manual is not squeezed into the same few lines as a 2-page memo.
MIN_SUMMARY_CHARS = 300
MAX_SUMMARY_CHARS = 1500


def target_summary_chars(text_len: int) -> int:
    return max(MIN_SUMMARY_CHARS, min(MAX_SUMMARY_CHARS, text_len // 100))


class DocumentSummarizer:
    def __init__(
        self,
        get_model: Callable[[], BaseChatModel] | None = None,
        section_chars: int | None = None,
        batch_size: int | None = None,
        max_concurrency: int | None = None,
    ):
        # Resolved per call, not at import: LLMFactory reads settings and caches the model.
        self._get_model = get_model or LLMFactory.get_model
        self._section_chars = section_chars or settings.SUMMARY_SECTION_CHARS
        self._batch_size = batch_size or settings.SUMMARY_BATCH_SIZE
        if self._batch_size < 2:
            raise ValueError("batch_size must be at least 2, or collapsing never shrinks")
        self._max_concurrency = max_concurrency or settings.SUMMARY_MAX_CONCURRENCY
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=self._section_chars,
            chunk_overlap=0,  # overlap would only repeat facts in the partial summaries
            separators=["\n\n", "\n", "。", ". ", " ", ""],
        )

    async def summarize(self, text: str) -> str:
        text = text.strip()
        if not text:
            raise ValueError("cannot summarize an empty document")
        target_chars = target_summary_chars(len(text))
        sections = self._splitter.split_text(text)
        if len(sections) == 1:
            return await self._complete(DOCUMENT_SUMMARY_REDUCE_PROMPT, sections[0], target_chars)

        sem = asyncio.Semaphore(self._max_concurrency)
        # Each partial gets an even share of one section, so a full batch fits the next call.
        map_chars = max(MIN_SUMMARY_CHARS, self._section_chars // self._batch_size)

        async def guarded_map(part: str) -> str:
            async with sem:
                return await self._complete(DOCUMENT_SUMMARY_MAP_PROMPT, part, map_chars)

        partials = list(await asyncio.gather(*(guarded_map(section) for section in sections)))
        # Collapse until everything fits one reduce call. Each round divides the count by
        # batch_size, so this terminates.
        while len(partials) > 1 and len(_join(partials)) > self._section_chars:
            batches = [
                _join(partials[i : i + self._batch_size])
                for i in range(0, len(partials), self._batch_size)
            ]
            partials = list(await asyncio.gather(*(guarded_map(batch) for batch in batches)))

        return await self._complete(DOCUMENT_SUMMARY_REDUCE_PROMPT, _join(partials), target_chars)

    async def _complete(self, prompt: ChatPromptTemplate, text: str, target_chars: int) -> str:
        messages = prompt.format_messages(text=text, target_chars=target_chars)
        response = await self._get_model().ainvoke(messages)
        # .text joins the text blocks; Gemini can return content as a list of parts.
        content = response.text
        if not content.strip():
            raise ValueError("LLM returned an empty summary")
        return content.strip()


def _join(parts: list[str]) -> str:
    return "\n\n".join(parts)
