"""Embedding router（cascade 第二層，~20ms）。

用向量相似度把 query 分到與它最接近的 `ROUTE_EXAMPLES` 範例所屬的類別。
`threshold=0.70` 與 cascade 的短路門檻共用同一個 `CONFIDENCE_THRESHOLD`
常數（`routing/taxonomy.py`），不可與 semantic cache 的門檻混用。

`score_route()` 額外對外開放，讓 `LLMRouter` 可以複用同一份範例向量，對 LLM
選出的 route 算「非自報」信心分數，不必重新維護一套相似度邏輯。
"""

from __future__ import annotations

import math
from typing import Protocol

from app.routing.router.base import BaseRouter
from app.routing.taxonomy import DEFAULT_ROUTE, CONFIDENCE_THRESHOLD, Route, RouteDecision, RouterContext

ROUTER_VERSION = "embedding-v1"

# 6 類、每類代表性例句。修改這份範例集必須遞增 ROUTER_VERSION。
ROUTE_EXAMPLES: dict[Route, list[str]] = {
    Route.LOOKUP: [
        "這份合約的付款條件是什麼？",
        "第三章提到的資安規範是什麼？",
        "我們的退款政策內容是什麼？",
        "請問使用手冊裡怎麼設定密碼？",
        "文件中對於資料保存期限的規定是什麼？",
        "What does the SLA document say about uptime guarantees?",
    ],
    Route.GLOBAL: [
        "這批文件整體在討論什麼主題？",
        "把所有會議記錄的重點摘要給我。",
        "這個知識庫涵蓋哪些主要領域？",
        "幫我總結今年所有季報的共同趨勢。",
        "這些文件加起來大概在講什麼？",
        "Summarize the key themes across all uploaded reports.",
    ],
    Route.METADATA: [
        "這份文件的作者是誰？",
        "這份報告是什麼時候上傳的？",
        "知識庫裡有哪些 PDF 類型的文件？",
        "這份文件的版本號是多少？",
        "誰是這份合約的最後修改者？",
        "List all documents uploaded after 2026-01-01.",
    ],
    Route.MULTI_HOP: [
        "去年跟今年的營收成長率差多少？",
        "比較A產品和B產品的規格差異。",
        "先找出負責這個專案的人，再查他還負責哪些專案。",
        "這兩份合約的付款條件哪裡不一樣？",
        "根據上一季的問題，找出這一季有沒有改善。",
        "Compare the security policies across all three vendor contracts.",
    ],
    Route.NO_RETRIEVAL: [
        "什麼是機器學習？",
        "牛頓第一運動定律是什麼？",
        "請幫我把這句話翻譯成英文。",
        "1+1等於多少？",
        "寫一首關於秋天的短詩。",
        "What is the capital of France?",
    ],
    Route.META: [
        "你能做什麼？",
        "你是誰？",
        "這個系統支援哪些功能？",
        "我可以問你什麼類型的問題？",
        "你是用什麼模型？",
        "What can you help me with?",
    ],
}


class EmbeddingsClient(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class GeminiEmbeddingsClient:
    """預設 embeddings 來源。測試請注入假的 `EmbeddingsClient`，避免打真的 API。"""

    def __init__(self, model=None):
        self._model = model

    def _get_model(self):
        if self._model is None:
            from langchain_google_genai import GoogleGenerativeAIEmbeddings

            self._model = GoogleGenerativeAIEmbeddings(model="models/text-embedding-004")
        return self._model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await self._get_model().aembed_documents(texts)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """公開給其他模組重用（例如 `routing/cache/semantic_cache.py`），避免重複實作。"""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class EmbeddingRouter(BaseRouter):
    router_version = ROUTER_VERSION

    def __init__(
        self,
        embeddings: EmbeddingsClient | None = None,
        examples: dict[Route, list[str]] | None = None,
        threshold: float = CONFIDENCE_THRESHOLD,
    ):
        self._embeddings = embeddings or GeminiEmbeddingsClient()
        self._examples = examples if examples is not None else ROUTE_EXAMPLES
        self._threshold = threshold
        self._example_vectors: dict[Route, list[list[float]]] | None = None

    async def _ensure_example_vectors(self) -> dict[Route, list[list[float]]]:
        """固定範例集的向量只算一次並快取，避免每次呼叫都重算。"""
        if self._example_vectors is None:
            routes = list(self._examples.keys())
            flat_texts = [text for route in routes for text in self._examples[route]]
            flat_vectors = await self._embeddings.embed(flat_texts)
            vectors_by_route: dict[Route, list[list[float]]] = {}
            idx = 0
            for route in routes:
                n = len(self._examples[route])
                vectors_by_route[route] = flat_vectors[idx : idx + n]
                idx += n
            self._example_vectors = vectors_by_route
        return self._example_vectors

    async def score_route(self, query: str, route: Route) -> float:
        """query 與指定 route 範例集的最大相似度，供 LLMRouter 做非自報信心評分複用。"""
        query_vec = (await self._embeddings.embed([query]))[0]
        example_vectors = await self._ensure_example_vectors()
        route_vectors = example_vectors.get(route, [])
        if not route_vectors:
            return 0.0
        best = max(cosine_similarity(query_vec, v) for v in route_vectors)
        return max(0.0, min(1.0, best))

    async def route(self, query: str, ctx: RouterContext) -> RouteDecision:
        query_vec = (await self._embeddings.embed([query]))[0]
        example_vectors = await self._ensure_example_vectors()

        best_route = DEFAULT_ROUTE
        best_score = 0.0
        for route, vectors in example_vectors.items():
            if not vectors:
                continue
            score = max(cosine_similarity(query_vec, v) for v in vectors)
            if score > best_score:
                best_score = score
                best_route = route

        confidence = max(0.0, min(1.0, best_score))
        return RouteDecision(
            route=best_route,
            confidence=confidence,
            reasoning=f"embedding: nearest example route={best_route.value} score={confidence:.3f}",
        )
