"""Semantic cache：route decision 快取，用向量相似度找過去語意相近的 query 直接
複用其 route decision，命中率比單純 exact-hash 高（尤其對同義改寫的重複問法）。

**租戶隔離**：cache key 一律含 tenant context（`tenant_id` + `knowledge_base_id`）。
不同租戶即使問一模一樣的句子，也不會互相命中彼此的快取——`get()`/`put()` 都先按
這個 key 過濾候選項目，不同租戶的向量永遠不會拿來互相比對相似度。

**相似度門檻**：`SEMANTIC_CACHE_THRESHOLD` 刻意不沿用 embedding router 的
`CONFIDENCE_THRESHOLD`（0.70）——快取誤命中的代價（把明顯不同語意的問題套用舊決策）
比分類信心不足的代價更高，門檻應該更嚴。這裡先給一個保守的**示範值**，上線前必須
用真實查詢分佈重新校準，不可直接拿來當最終結論。

**失效策略**（M6 明確決定並記錄）：
1. `router_version` 變更時，`get()` 只會在候選項目中比對「同一個 router_version」
   寫入的項目——版本一變，舊項目在查詢時直接被濾掉（不用主動刪除，效果等同立即
   失效）。
2. 另外疊加 `ttl_seconds`：處理「router_version 沒變，但知識庫內容已經更新，
   舊決策可能過時」這種情況，超過 TTL 的項目一樣會被濾掉。
3. `max_entries`：整個 cache 的項目上限，超過時丟掉最舊的項目。

**Experimental，還沒接進 graph**（`app/agent/graphs/routed_rag.py` 的 `RouterNode` 沒有使用它）。
"""

from __future__ import annotations

import time

from app.routing.router.embedding import EmbeddingsClient, FactoryEmbeddingsClient, cosine_similarity
from app.routing.taxonomy import RouteDecision, RouterContext

SEMANTIC_CACHE_THRESHOLD = 0.92
"""示範值，尚未用真實查詢分佈校準，上線前必須重新校準，不可沿用 embedding router 的 0.70。"""

DEFAULT_TTL_SECONDS = 3600.0

DEFAULT_MAX_ENTRIES = 1024
"""整個 cache（所有租戶合計）的項目上限，超過時丟掉最舊的項目。"""


class _CacheEntry:
    __slots__ = ("cache_key", "vector", "decision", "router_version", "created_at")

    def __init__(self, cache_key, vector, decision, router_version, created_at):
        self.cache_key = cache_key
        self.vector = vector
        self.decision = decision
        self.router_version = router_version
        self.created_at = created_at


def _tenant_cache_key(ctx: RouterContext) -> str:
    return f"{ctx.tenant_id}:{ctx.knowledge_base_id}"


class SemanticCache:
    def __init__(
        self,
        embeddings: EmbeddingsClient | None = None,
        threshold: float = SEMANTIC_CACHE_THRESHOLD,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        clock=time.monotonic,
    ):
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._embeddings = embeddings or FactoryEmbeddingsClient()
        self._threshold = threshold
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._entries: list[_CacheEntry] = []

    async def embed_query(self, query: str) -> list[float]:
        """呼叫端在 miss 時把這個 vector 傳給 `put()`，同一個 query 只 embed 一次。"""
        return (await self._embeddings.embed([query]))[0]

    async def get(
        self,
        query: str,
        ctx: RouterContext,
        router_version: str,
        query_vector: list[float] | None = None,
    ) -> RouteDecision | None:
        cache_key = _tenant_cache_key(ctx)
        now = self._clock()
        candidates = [
            entry
            for entry in self._entries
            if entry.cache_key == cache_key
            and entry.router_version == router_version
            and (now - entry.created_at) <= self._ttl_seconds
        ]
        if not candidates:
            return None

        query_vec = query_vector or await self.embed_query(query)
        best_entry = None
        best_score = 0.0
        for entry in candidates:
            score = cosine_similarity(query_vec, entry.vector)
            if score > best_score:
                best_score = score
                best_entry = entry

        if best_entry is not None and best_score >= self._threshold:
            return best_entry.decision
        return None

    async def put(
        self,
        query: str,
        ctx: RouterContext,
        router_version: str,
        decision: RouteDecision,
        query_vector: list[float] | None = None,
    ) -> None:
        cache_key = _tenant_cache_key(ctx)
        query_vec = query_vector or await self.embed_query(query)
        # 寫入前先清掉過期項目，再把超過上限的最舊項目丟掉（`_entries` 依寫入時間排序）
        self.purge_expired()
        overflow = len(self._entries) + 1 - self._max_entries
        if overflow > 0:
            del self._entries[:overflow]
        self._entries.append(
            _CacheEntry(
                cache_key=cache_key,
                vector=query_vec,
                # 降級紀錄只屬於產生它的那一輪；命中時帶回去會讓 trace 出現這一輪沒發生的降級
                decision=decision.model_copy(update={"degradations": []}),
                router_version=router_version,
                created_at=self._clock(),
            )
        )

    def purge_expired(self) -> int:
        """清掉過期項目，回傳清掉的筆數。`put()` 每次寫入前都會呼叫。"""
        now = self._clock()
        before = len(self._entries)
        self._entries = [e for e in self._entries if (now - e.created_at) <= self._ttl_seconds]
        return before - len(self._entries)
