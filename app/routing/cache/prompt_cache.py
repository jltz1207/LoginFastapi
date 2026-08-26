"""Prompt caching：把固定的 taxonomy instructions/few-shot 前綴（`routing/router/llm.py`
的 `_FIXED_PREFIX`）快取在 Gemini 端（Explicit Context Caching），逐次呼叫只需要送
變動的 query，省下重複貼分類定義的 TTFT/token 成本。

**嚴格規則**（CLAUDE.md 開發約定）：固定前綴不得混入任何逐請求變動內容（對話歷史、
query 本身）。呼叫端（`llm.py`）負責保證傳進來的 `fixed_content` 是真的固定不變的
字串；這個模組不做、也做不到偵測防呆。

**失效策略**（M6 明確決定並記錄）：cache key 直接把 `router_version` 編進去——修改
prompt/`ROUTE_EXAMPLES` 時遞增 `router_version`（既有規則）會自然產生新的 cache
key，新版本自動不會用到舊快取，不需要額外主動失效/刪除的邏輯；舊的 Gemini
CachedContent 讓它自己依 `ttl_seconds` 過期即可。

**已知限制**：Gemini 的 explicit context caching 對可快取內容有最小 token 數門檻，
目前 `_FIXED_PREFIX`（instructions + 36 條 few-shot 例句）大概率不到門檻，實際對
Gemini API 呼叫 `caches.create()` 可能會被拒絕。因此 `PromptCache.get_cache_handle()`
的呼叫端（`llm.py`）必須把它當成「可能失敗的最佳化」處理：失敗就退回不快取的路徑，
不能讓這個最佳化本身變成一個新的故障點。等 few-shot 範例集長大到門檻以上，或改用
Gemini 的隱式快取（無最小門檻，新版模型自動生效）之後，這個限制才會解除。
"""

from __future__ import annotations

from typing import Protocol

CACHE_INVALIDATION_POLICY = (
    "cache key 綁 router_version；版本遞增即自然產生新 key，不主動刪除舊快取，"
    "靠 Gemini 端的 TTL 自動過期。"
)

DEFAULT_TTL_SECONDS = 3600


class CacheBackend(Protocol):
    async def get_or_create(self, cache_key: str, fixed_content: str, ttl_seconds: int) -> str:
        """回傳可在後續呼叫中引用的 cache handle（Gemini 是 CachedContent 的 resource name）。"""
        ...


class GeminiCacheBackend:
    """預設實作：用 google-genai 的 Explicit Caching API（`client.aio.caches.create`）。"""

    def __init__(self, client=None, model: str = "models/gemini-2.5-flash"):
        self._client = client
        self._model = model

    def _get_client(self):
        if self._client is None:
            from google import genai

            self._client = genai.Client()
        return self._client

    async def get_or_create(self, cache_key: str, fixed_content: str, ttl_seconds: int) -> str:
        from google.genai import types

        client = self._get_client()
        cache = await client.aio.caches.create(
            model=self._model,
            config=types.CreateCachedContentConfig(
                display_name=cache_key,
                contents=[fixed_content],
                ttl=f"{ttl_seconds}s",
            ),
        )
        return cache.name


class PromptCache:
    """對外入口：`get_cache_handle()` 依 `router_version` 拿到（或建立）快取 handle。
    同一個 process 內對同一個 `router_version` 只會呼叫 backend 一次，之後都是查表。
    """

    def __init__(self, backend: CacheBackend | None = None, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self._backend = backend or GeminiCacheBackend()
        self._ttl_seconds = ttl_seconds
        self._handles: dict[str, str] = {}

    @staticmethod
    def cache_key(router_version: str) -> str:
        return f"router-prompt-cache:{router_version}"

    async def get_cache_handle(self, router_version: str, fixed_content: str) -> str:
        key = self.cache_key(router_version)
        if key not in self._handles:
            self._handles[key] = await self._backend.get_or_create(key, fixed_content, self._ttl_seconds)
        return self._handles[key]
