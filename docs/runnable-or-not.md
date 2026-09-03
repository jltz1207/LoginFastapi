# Retriever 該回傳 `Runnable` 還是 `list[Document]`？（Lazy vs Eager）

決定 `app/rag/retriever/base.py::RetrieverFactory` 這個抽象介面的形狀。這個選擇會連帶決定 BM25 索引的成本、能不能接 reranker、以及 async 語意對不對。

## 兩種形狀

### Eager（回傳結果）

介面負責「**做完檢索**」，query 是呼叫參數：

```python
class RetrieverFactory(abc.ABC):
    @abstractmethod
    async def retrieve(
        self, query: str, user_id: str, knowledge_base_id: str, top_k: int = 8
    ) -> list[Document]: ...


class HybridRetriever(RetrieverFactory):
    async def retrieve(self, query, user_id, knowledge_base_id, top_k=8):
        documents = indexer.get_all_documents_in_kb(user_id, knowledge_base_id)
        bm25 = BM25Retriever.from_documents(documents)      # ← 每次呼叫都重建
        dense = get_collection_retriever(user_id, knowledge_base_id, top_k=top_k)
        ensemble = EnsembleRetriever(retrievers=[bm25, dense], weights=self.weights)
        docs = await ensemble.ainvoke(query)
        return docs[:top_k]
```

呼叫端：

```python
docs = await HybridRetriever().retrieve(query, user_id, kb_id, top_k=8)
```

### Lazy（回傳可執行物件）

介面負責「**組裝好一條檢索管線**」，query 留到執行時才傳：

```python
class RetrieverFactory(abc.ABC):
    @abstractmethod
    async def get_retriever(
        self, user_id: str, knowledge_base_id: str, top_k: int = 8
    ) -> Runnable: ...      # Runnable[str, list[Document]]


class HybridRetriever(RetrieverFactory):
    async def get_retriever(self, user_id, knowledge_base_id, top_k=8) -> Runnable:
        documents = indexer.get_all_documents_in_kb(user_id, knowledge_base_id)
        if not documents:
            return get_collection_retriever(user_id, knowledge_base_id, top_k=top_k)
        bm25 = BM25Retriever.from_documents(documents)      # ← 只建一次
        bm25.k = top_k
        dense = get_collection_retriever(user_id, knowledge_base_id, top_k=top_k)
        ensemble = EnsembleRetriever(retrievers=[bm25, dense], weights=self.weights)
        return ensemble | RunnableLambda(lambda docs: docs[:top_k])
```

呼叫端：

```python
retriever = await HybridRetriever().get_retriever(user_id, kb_id, top_k=8)
docs = await retriever.ainvoke(query)          # 可以重複 invoke，不用重建
```

**命名對照**：方法叫 `retrieve` 就該是 eager，叫 `get_retriever` / `build_*` 就該是 lazy。名字跟行為對不上是常見的混淆來源。

## 逐項差別

| | Eager (`list[Document]`) | Lazy (`Runnable`) |
|---|---|---|
| query 何時提供 | 呼叫方法時 | `ainvoke()` 時 |
| 建構成本何時付 | **每次查詢都付一次** | 建一次，之後每次 invoke 都免費 |
| 接 reranker | ❌ 接不上（見下） | ✅ 直接餵給 `ContextualCompressionRetriever` |
| 多輪 / multi-hop 查詢 | 每一 hop 重建索引 | 建一次，跑 N 個 sub-query |
| LCEL 組合（`\|`） | ❌ 不能 | ✅ 可以再往上疊任何 Runnable |
| LangSmith trace | 只看到一個黑盒函式 | 每個 step 各自一個 span |
| 測試 | 要 mock 掉整個方法 | 可以注入假 Runnable，或單測 pipeline 各段 |
| 心智負擔 | 低，一步到位 | 略高，多一層「先組裝再執行」 |

## 三個關鍵理由（針對這個 repo）

### 1. `HybridRetriever` 的建構成本非常高，不能每次查詢重付

BM25 沒有辦法建在 Chroma 裡，只能在記憶體算。所以 `HybridRetriever` 一定要：

1. `get_all_documents_in_kb()` — 把該 KB 的**所有 chunk** 從 Chroma 撈回來
2. `BM25Retriever.from_documents()` — 對全部 chunk 做 tokenize + 建倒排索引

Eager 形狀下，這兩步**每個 query 都會重跑一次**。1000 個 chunk 的 KB 就是每次查詢多幾百毫秒到數秒，而且是純浪費 — 同一個 KB 的索引在文件沒變之前完全一樣。

Lazy 形狀把這筆成本關在 `get_retriever()` 裡，呼叫端拿到 Runnable 後可以留著重複用。這是 lazy 最主要的動機。

（對照組：`BasicRetriever` 只是 `collection.as_retriever(...)`，本身就沒有建構成本，兩種形狀對它沒差 — 但介面要統一，得遷就成本高的那個。）

### 2. Reranker 只吃 `Runnable`，不吃 `list[Document]`

`app/rag/reranker/reranker.py` 用的 `ContextualCompressionRetriever`（`langchain_classic/retrievers/contextual_compression.py:19`）欄位型別是：

```python
base_retriever: RetrieverLike
# langchain_core/retrievers.py:35 → RetrieverLike = Runnable[str, list[Document]]
```

它要的是一個**還沒執行的檢索器**，因為它得先拿 query 去執行檢索、再把 query + 結果一起交給 compressor 重排。如果 retriever 已經把結果算完回傳成 `list[Document]`，reranker 就拿不到 query 了 — 只能在外面手工再組一次 `RunnableParallel(query=..., documents=...)`，等於重新發明 `ContextualCompressionRetriever`。

Lazy 形狀下，rerank 就是一行：

```python
base = await HybridRetriever().get_retriever(user_id, kb_id, top_k=8)
pipeline = with_rerank(base, rerank_top_k=4)     # 仍然是 Runnable[str, list[Document]]
docs = await pipeline.ainvoke(query)
```

**two-stage 的意義**：第一階段用 `top_k=8` 寬鬆撈（recall 優先），第二階段 rerank 收斂到 4 筆（precision 優先）餵給 generator。Eager 形狀要做到同樣的事，得把兩個階段的參數硬塞進同一個方法簽名。

### 3. Multi-hop 分支會對同一個 KB 連續查很多次

`app/routing/branches/multi_hop.py` 的模式是把一個問題拆成多個 sub-query，逐個檢索再合併。Lazy 形狀下是「建一次 retriever，跑 N 個 sub-query」；eager 形狀下是「重建 N 次 BM25 索引」。差距隨 hop 數線性放大。

## Eager 什麼時候才是對的

不是說 eager 一定差。以下情況 eager 更合適：

- 檢索器**沒有建構成本**（例如只有 dense，`as_retriever()` 是個薄包裝）
- 呼叫端**只會查一次就丟掉**，沒有重用機會
- 不需要往上疊 rerank / compression / 其他 LCEL step
- 想要最單純的介面，不想讓呼叫端理解「先組裝再執行」

如果這個專案只有 `BasicRetriever`，eager 是完全合理的選擇。是 `HybridRetriever` 的 BM25 成本 + reranker 的介面需求把天秤壓向 lazy。

## 結論

**這個 repo 用 lazy**：

```python
# app/rag/retriever/base.py
class RetrieverFactory(abc.ABC):
    @abstractmethod
    async def get_retriever(
        self, user_id: str, knowledge_base_id: str, top_k: int = 8
    ) -> Runnable: ...
```

配套規則：

- 兩個子類（`BasicRetriever` / `HybridRetriever`）的簽名必須**完全一致**，否則抽象基底類別失去意義 — 呼叫端沒辦法拿 `RetrieverFactory` 型別的變數做多型呼叫。
- 回傳型別統一是 `Runnable[str, list[Document]]`，`HybridRetriever` 要靠 `| RunnableLambda(lambda docs: docs[:top_k])` 收斂筆數，因為 `EnsembleRetriever` 的 RRF **不做截斷**（詳見 `docs/plans/ensemble-retriever-topk-rrf-walkthrough.md`）。
- 呼叫端一律 `await retriever.ainvoke(query)`，不要用同步的 `.invoke()` — 底層會打 Gemini embedding API，同步呼叫會阻塞整個 FastAPI event loop。
- 若呼叫端會重複查詢（multi-hop、agent 迴圈），**要把 `get_retriever()` 的結果存起來重用**，不要每次重建；否則 lazy 的好處等於沒拿到。
