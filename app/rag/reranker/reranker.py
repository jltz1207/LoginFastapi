import asyncio
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from flashrank import Ranker, RerankRequest
from langchain_core.documents import Document
from langchain_core.runnables import Runnable, RunnableLambda, RunnableParallel, RunnablePassthrough

DEFAULT_RERANK_TOP_K = 4

# Picked by measurement, not by name. On a 5-case Traditional Chinese benchmark
# (top-1 accuracy): MiniLM-L-12-v2 5/5, MultiBERT-L-12 3/5, TinyBERT-L-2-v2 2/5.
# The nominally multilingual "MultiBERT" and flashrank's own default "TinyBERT"
# both collapse to near-identical scores on Chinese and rank almost arbitrarily.
FLASHRANK_MODEL = "ms-marco-MiniLM-L-12-v2"

# flashrank defaults cache_dir to "/tmp", which resolves to C:\tmp on Windows.
# Pin it to a conventional per-user cache instead.
FLASHRANK_CACHE_DIR = str(Path.home() / ".cache" / "flashrank")


class Reranker(Protocol):
    async def rerank(self, query: str, documents: list[Document], top_k: int = DEFAULT_RERANK_TOP_K) -> list[Document]: ...


class PassthroughReranker:
    """沒有接真正 rerank 模型時的預設實作：Chroma 回傳順序本身已經是相似度排序，
    這裡只負責截斷 top_k，保留介面讓之後要換真正的 rerank 模型時
    (例如 cross-encoder) 呼叫端完全不用改。"""

    async def rerank(self, query: str, documents: list[Document], top_k: int = DEFAULT_RERANK_TOP_K) -> list[Document]:
        return documents[:top_k]


@lru_cache(maxsize=None)
def _get_ranker(model_name: str, cache_dir: str, max_length: int) -> Ranker:
    """Ranker 建構時會載入 ONNX session，首次還會從 HuggingFace 下載模型（約 100MB），
    所以一定要 cache——每個 request 建一次會慢到無法使用。"""
    return Ranker(
        model_name=model_name,
        cache_dir=cache_dir,
        max_length=max_length,
        log_level="WARNING",
    )


class FlashrankReranker:
    """本地 ONNX cross-encoder rerank，無 API key、無按次費用。

    跟 retriever 的分工：dense / BM25 都是 bi-encoder，query 與 document 各自獨立編碼
    再比相似度，快但看不到兩者的交互作用；cross-encoder 把 (query, document) 成對餵進
    模型一起編碼，精度高很多，但成本正比於候選數量——所以只能放在 retrieval 之後對少量
    候選（8~20 筆）重排，不能拿來掃全庫。

    典型用法是 two-stage：retriever 用 top_k=8 寬鬆撈（recall 優先），
    reranker 收斂到 top_k=4 餵給 generator（precision 優先）。

    建構本身很便宜（不載入模型），模型在第一次 rerank() 時才 lazy 載入。
    """

    def __init__(
        self,
        model_name: str = FLASHRANK_MODEL,
        cache_dir: str = FLASHRANK_CACHE_DIR,
        max_length: int = 512,
    ):
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.max_length = max_length

    async def rerank(self, query: str, documents: list[Document], top_k: int = DEFAULT_RERANK_TOP_K) -> list[Document]:
        if not documents:
            return []
        # ONNX 推論是 CPU-bound 的同步呼叫，直接在 event loop 裡跑會卡住整個 FastAPI process。
        return await asyncio.to_thread(self._rerank_sync, query, documents, top_k)

    def _rerank_sync(self, query: str, documents: list[Document], top_k: int) -> list[Document]:
        ranker = _get_ranker(self.model_name, self.cache_dir, self.max_length)

        # 只把 page_content 送進模型，metadata 留在這邊用 index 對回去——
        # 這樣原始 Document 的 metadata 保證原封不動，不依賴 flashrank 的 meta 轉送。
        passages = [{"id": index, "text": doc.page_content} for index, doc in enumerate(documents)]
        ranked = ranker.rerank(RerankRequest(query=query, passages=passages))[:top_k]

        results: list[Document] = []
        for passage in ranked:
            original = documents[passage["id"]]
            results.append(
                Document(
                    page_content=original.page_content,
                    # score 是 numpy.float32，轉成內建 float 才能安全序列化進 graph state / JSON。
                    metadata={**original.metadata, "relevance_score": float(passage["score"])},
                )
            )
        return results


def with_rerank(
    retriever: Runnable, reranker: Reranker | None = None, rerank_top_k: int = DEFAULT_RERANK_TOP_K
) -> Runnable:
    """把任一個 RetrieverFactory.get_retriever() 回傳的 Runnable 疊上 rerank，
    回傳一樣是 Runnable[str, list[Document]]，呼叫端用法不變。"""
    reranker = reranker or FlashrankReranker()

    async def _rerank(inputs: dict) -> list[Document]:
        return await reranker.rerank(inputs["query"], inputs["documents"], top_k=rerank_top_k)

    return RunnableParallel(query=RunnablePassthrough(), documents=retriever) | RunnableLambda(_rerank)
