# 把 hybrid retriever / reranker 抽成 `app/rag/` 底下的可重用 class

## Context

`app/routing/branches/lookup.py`（在 [`integrate-searching-rag-into-lookup.md`](./integrate-searching-rag-into-lookup.md) 那份計劃裡會被刪除）裡有一組其實設計得不錯、只是接錯基礎設施的元件：`HybridRetriever`/`ChromaHybridRetriever`（Protocol + 實作）跟 `Reranker`/`PassthroughReranker`。問題是它自己 `import chromadb; chromadb.Client()` 開一個全新、非持久化的 in-memory client，跟 `app/vectorstore/collection_manager.py` 的 `Collection_manager`（`app/rag/retriever.py` 現有的 `get_collection_retriever()` 已經在正確用）完全脫節，查不到真正 ingest 進去的資料。

現況 `app/rag/retriever.py` 只有一個裸函式 `get_collection_retriever(user_id, search_type, search_kwags)`，回傳 LangChain 的 `VectorStoreRetriever`，沒有 rerank 這一步。這次要做的：把「檢索 + rerank」這個能力正式做成 `app/rag/` 底下的可重用 class（用 `Collection_manager` 接正確的持久化 Chroma client），取代 `branches/lookup.py` 裡那組跑不到真資料的版本，也讓 `app/routing/branches/multi_hop.py` 現在同樣打裸 `chromadb.Client()` 的 `ChromaHopRetriever` 有東西可以換掉——這正好是[整合 LOOKUP 那份計劃](./integrate-searching-rag-into-lookup.md)裡明確列為「這次不做」、留給之後處理的兩個問題之一。

依賴：`multi_hop.py` 這邊的改動要讀 `state.user_id`（`Collection_manager.get_or_create_collection()` 是照 `user_id` 分 collection），這個欄位要等 LOOKUP 那份計劃把 `RoutedAgentState` 改成繼承 `AgentState`、`branches/*.py` 改成 attribute 存取之後才自然拿得到；順序上建議先做完 LOOKUP 整合，再做這份。

## 核心設計

### 1. `app/rag/retriever.py`（擴充既有檔案，不新開檔）

新增 `Retriever` Protocol + `ChromaHybridRetriever` 實作，沿用 `branches/lookup.py` 原本的介面設計，但改注入 `Collection_manager`：

```python
class Retriever(Protocol):
    async def retrieve(
        self, query: str, user_id: str, knowledge_base_id: str, top_k: int = 8
    ) -> list[Document]: ...


class ChromaHybridRetriever:
    """對使用者的 Chroma collection 做向量檢索，query 一律連同
    knowledge_base_id metadata filter 一起送出。命名沿用 branches/lookup.py 的
    'hybrid'：目前只有 dense vector 檢索，之後要疊加 sparse/BM25 就在這支擴充，
    呼叫端介面不用變。"""

    def __init__(self, collection_manager: Collection_manager | None = None):
        self._collection_manager = collection_manager or Collection_manager()

    async def retrieve(
        self, query: str, user_id: str, knowledge_base_id: str, top_k: int = 8
    ) -> list[Document]:
        collection = self._collection_manager.get_or_create_collection(user_id)
        results = await collection.asimilarity_search_with_relevance_scores(
            query, k=top_k, filter={"knowledge_base_id": knowledge_base_id}
        )
        docs = []
        for doc, score in results:
            doc.metadata["score"] = score
            docs.append(doc)
        return docs
```

用 `asimilarity_search_with_relevance_scores`（`langchain_chroma.Chroma` 繼承自 `VectorStore` 基底類別，沒有自己的 async 實作時，LangChain 會自動用 thread executor 包一層，不會擋住 event loop）而不是 `.as_retriever()`，是為了把相似度分數寫進 `doc.metadata["score"]`——現在 `PassthroughReranker` 用不到，但之後要換真正的 cross-encoder rerank 模型時，分數已經在 metadata 上，呼叫端不用改。

既有的 `get_collection_retriever()`/`format_doc_to_string()` 原樣保留（`app/rag/pipelines.py`、`app/agent/nodes/retrieval.py` 還在用），這次不動。

### 2. `app/rag/reranker.py`（新檔案）

```python
class Reranker(Protocol):
    async def rerank(self, query: str, documents: list[Document], top_k: int = 4) -> list[Document]: ...


class PassthroughReranker:
    """沒有接真正 rerank 模型時的預設實作：Chroma 回傳順序本身已經是相似度排序，
    這裡只負責截斷 top_k，保留介面讓之後要換真正的 rerank 模型時
    (例如 cross-encoder) 呼叫端完全不用改。"""

    async def rerank(self, query: str, documents: list[Document], top_k: int = 4) -> list[Document]:
        return documents[:top_k]
```

`Reranker` 單獨開一支檔案，不跟 `retriever.py` 合併：跟 `app/rag/` 現有分法一致（`chains/`、`loaders/`、`prompts/` 都是各自獨立的關注點），也讓之後要換真正的 rerank 模型時改動範圍侷限在這一支。

### 3. `app/routing/branches/multi_hop.py` — 換掉 `ChromaHopRetriever`

`MultiHopBranch` 建構子預設的 `retriever` 從 `ChromaHopRetriever()`（打裸 `chromadb.Client()`）換成用新的 `app.rag.retriever.ChromaHybridRetriever` + `app.rag.reranker.PassthroughReranker` 組出來的 adapter：`HopRetriever` protocol 要求回傳 `list[Chunk]`（`branches/common.py` 的型別），但 `ChromaHybridRetriever` 回傳 `list[Document]`——在 multi_hop.py 這邊做一個小 adapter 把 `Document` 轉成 `Chunk`（`Chunk(chunk_id=doc.metadata.get("id", ""), content=doc.page_content, score=doc.metadata.get("score", 0.0), metadata=doc.metadata)`），不動 `HopResult`/`Chunk` 本身，維持跟[LOOKUP 整合計劃]裡「這次不統一 `Chunk`/`Document` 型別」的決定一致，只在 multi_hop.py 這個呼叫邊界做轉換。呼叫時要帶 `state.user_id`（`str()` 轉型），這個欄位在 LOOKUP 整合計劃把 `RoutedAgentState` 改成繼承 `AgentState` 之後就有。

`branches/lookup.py` 刪除後，它原本的 `ChromaHybridRetriever`/`PassthroughReranker`/`HybridRetriever`/`Reranker` 這幾個名字就空出來了，不會跟這次新增的 `app/rag/` 版本撞名。

## 不在這次範圍內

- `app/agent/nodes/retrieval.py`（`searching_rag.py` 用的既有 LOOKUP 檢索）不改：它現在用 `get_collection_retriever()`，沒有 rerank 這一步，要不要換成 `ChromaHybridRetriever` + 加 rerank 是獨立決定，不因為這次新增了可重用 class 就順手改掉。
- 不接真正的 rerank 模型（cross-encoder 之類）：`PassthroughReranker` 這次只是把介面留好，`score` 寫進 metadata 供之後使用，不實作真正的重排邏輯。
- `branches/global_summary.py` 打裸 `chromadb.Client()` 的問題（跟 `ChromaSummaryIndexClient` 有關，查的是 summary collection 不是文件 chunk，跟這次的 `ChromaHybridRetriever` 用途不同）不在這次處理。

## 異動後的目錄結構

```
app/
├── rag/
│   ├── retriever.py                   [MOD] 新增 Retriever Protocol + ChromaHybridRetriever；
│   │                                         get_collection_retriever()/format_doc_to_string() 不動
│   └── reranker.py                    [NEW] Reranker Protocol + PassthroughReranker
│
└── routing/
    └── branches/
        └── multi_hop.py               [MOD] ChromaHopRetriever → app.rag 的 ChromaHybridRetriever
                                              + PassthroughReranker，呼叫端加 Document→Chunk adapter
```

## 驗證方式

1. **單元測試**：`ChromaHybridRetriever`/`PassthroughReranker` 各補 1-2 支測試，注入假的 `Collection_manager`（回傳一個假 `Chroma`/假 vectorstore），不打真的 Chroma/embedding API——延續 `routing/` 現有測試風格（DI + Protocol 注入假物件）。
2. **手動驗證**：在已經有資料 ingest 過的 knowledge base 上，直接呼叫 `ChromaHybridRetriever().retrieve(query, user_id, knowledge_base_id)`，確認拿到的 `Document` 內容跟 `get_collection_retriever()` 現有路徑查到的一致（同一份資料、同一個 collection，只是多了 `metadata["score"]`）。
3. **multi_hop 回歸**：`GRAPH_STRATEGY=4`（`ROUTED`，等 LOOKUP 整合計劃跑完後）丟一個會觸發 MULTI_HOP 的比較性問題，確認每一跳的 `trace` 顯示有查到 `documents`，跟改動前（裸 `chromadb.Client()`，理論上查不到真資料）行為明顯不同。
