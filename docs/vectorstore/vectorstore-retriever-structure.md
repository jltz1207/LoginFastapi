# Vector store 與 retriever 現況結構

這份文件描述 `app/vectorstore/**`、`app/rag/retriever/**`、`app/rag/reranker/**` 目前**實際長什麼樣**，以及誰在消費它們。
關於「該怎麼切 collection」的設計決策與演進方向，見 `vectorstore-collection-partitioning.md`；
關於 `get_or_create_collection()` 每次呼叫的成本量測，見 `vectorstore-collection-handle-cost.md`。
這裡只記現況與已知缺陷。

---

## 整體分層

```
ingestion:  POST /knowledgeBases/upload
              → base_loader.load()
              → ChunkerFactory → RecursiveChunks (chunk_size=500, overlap=100)
              → clean_chunks()
              → StoreIndexer.add_documents()
              → Chroma

retrieval:  retrieval_execution (agent node)
              → HybridRetriever.get_retriever()  ┬→ BM25Retriever (每個 request 現場建)
                                                 └→ get_collection_retriever() (dense)
              → EnsembleRetriever (0.6 / 0.4) → [:top_k]
              → with_rerank() → FlashrankReranker (本地 ONNX cross-encoder)
              → list[Chunk]
```

---

## Vector store 層（`app/vectorstore/`）

- 三個檔案，職責切得很乾淨。

### `client.py`

`Chroma_db` 包一個 `chromadb.PersistentClient(path=settings.CHROMA_PERSIST_DIRECTORY)`（`client.py:7`），
外面用 `@lru_cache` 的 `get_chroma_db()` 做 process-wide singleton。預設路徑 `./chroma_data`（`app/core/config.py:14`）。

---
### `collection_manager.py`

**唯一決定「collection 怎麼切」的地方。** `vector_partition_strategy` enum 三個值（`collection_manager.py:8-11`），
由 `settings.PARTITION_METHOD` 選：

| 值 | 策略 | collection 名 |
|---|---|---|
| 1 | `GLOBAL_COLLECTION` | `{COLLECTION_NAME}` |
| 2 | `ONE_USER_ONE_COLLECTION`（預設） | `{COLLECTION_NAME}_{user_id}` |
| 3 | `ONE_TENANT_ONE_COLLECTION` | `{COLLECTION_NAME}_{tenant_id}` |

`get_or_create_collection(user_id, tenant_id)` 回傳的是 **LangChain 的 `Chroma` wrapper**（不是原生 chromadb collection），
embedding function 來自 `EmbeddingFactory`。

---
### `StoreIndexer.py`
```
StoreIndexer 是被設計成唯一入口（add_documents / query / get_all_documents_in_kb 這組 API 形狀就是這個意圖）
```
三個方法：

- `add_documents()`（`StoreIndexer.py:10-13`）：把 caller 傳的 metadata 用 `metadata | doc.metadata` 合併進每個 chunk 後寫入。
- `query()`（`StoreIndexer.py:15-17`）：空殼，只寫了 `user_collection.query` 沒有呼叫，是死碼。
- `get_all_documents_in_kb()`（`StoreIndexer.py:19-34`）：`where` 用 `$and` 同時過濾
  `knowledge_base_id` / `user_id` / `tenant_id`，專門餵給 BM25。
  程式碼裡的註解明確說了理由——dense 那半已經三層都過濾，sparse 這半不補齊的話會洩漏出 dense 看不到的文件。

`get_vector_store_indexer()` 是 `lru_cache(maxsize=1)` singleton。

---

## Retriever 層（`app/rag/retriever/`）

### `base.py`

`RetrieverFactory` ABC，唯一方法（`base.py:8-10`）：

```python
async def get_retriever(self, tenant_id, user_id, knowledge_base_id, top_k: int = 8) -> Runnable
```

注意回傳型別是 `Runnable` 而不是 `BaseRetriever`——所以實作可以自由在尾巴疊 `RunnableLambda`。

### `common.py`

`get_collection_retriever()`（`common.py:4-22`）是 dense 檢索的共同入口，也是**租戶邊界真正被 enforce 的地方**：

```python
where = {"$and": [
    {"knowledge_base_id": knowledge_base_id},
    {"user_id": user_id},
    {"tenant_id": tenant_id},
]}
return collection.as_retriever(
    search_type=search_type,
    search_kwargs={"k": top_k, "filter": where},
)
```

支援 `extra_filter` 追加條件、`search_type` 可切 `similarity` / `mmr`。
另有 `format_doc_to_string()`（`common.py:24-26`）給 legacy LCEL path 用。

### `basic_retriever.py`

純 dense，就是 `get_collection_retriever(..., search_type="similarity")` 的薄包裝（`basic_retriever.py:7-11`）。
目前沒有任何地方在用。

### `hybrid_retriever.py`

目前 agent 實際走的路（`hybrid_retriever.py:9-24`）：

1. `get_all_documents_in_kb()` 撈出整個 KB 的 chunk，`BM25Retriever.from_documents()` 現場建 sparse index
2. `get_collection_retriever()` 拿 dense retriever
3. `EnsembleRetriever` 用 RRF 融合，權重由建構子傳入
4. 尾巴接 `RunnableLambda(lambda docs: docs[:top_k])` 截斷——因為 ensemble 融合後筆數會超過 `top_k`

---

## Rerank（`app/rag/reranker/reranker.py`）

`Reranker` 是 `Protocol`，兩個實作：`PassthroughReranker`（只截斷 top_k，保留介面）與
`FlashrankReranker`（本地 ONNX cross-encoder）。幾個值得記住的設計：

- **模型選擇是量測出來的，不是看名字。** 檔頭註解記了在 5 題繁中 benchmark 上
  `ms-marco-MiniLM-L-12-v2` 拿 5/5，而名字聽起來多語的 MultiBERT 只有 3/5、flashrank 自己的預設 TinyBERT 只有 2/5（`reranker.py:12-16`）。
- `_get_ranker()` 用 `lru_cache` 快取 ONNX session（首次會從 HuggingFace 下載約 100MB），
  每個 request 建一次會慢到無法使用（`reranker.py:36-45`）。
- `rerank()` 用 `asyncio.to_thread()` 把 CPU-bound 推論丟出 event loop，否則會卡住整個 FastAPI process（`reranker.py:72-76`）。
- 只送 `page_content` 進模型，靠 index 對回原始 `Document`，metadata 保證原封不動；
  `relevance_score` 從 `numpy.float32` 轉成內建 `float` 才能序列化進 graph state（`reranker.py:78-96`）。
- `cache_dir` 明確指向 `~/.cache/flashrank`——flashrank 預設是 `/tmp`，在 Windows 上會解析成 `C:\tmp`（`reranker.py:18-20`）。
- `with_rerank()`（`reranker.py:99-109`）是組合器，接任何 `Runnable[str, list[Document]]`：

```python
RunnableParallel(query=RunnablePassthrough(), documents=retriever) | RunnableLambda(_rerank)
```

---

## 兩條消費路徑

### Agent path（主線）

`app/agent/nodes/retrieval.py:10-22`。`HybridRetriever((0.6, 0.4))`（dense 偏重）搭 `top_k=4`，
再 `with_rerank(rerank_top_k=4)`。query 優先序是 `standalone_query → resolved_query → query`。

它同時是 graph 的 entry node，所以負責重設 `tool_calls_count` / `token_count` 的 per-run budget——
checkpointer 依 `thread_id` 存 state，不重設的話會跨輪累積、幾個訊息後就把預算用光。

輸出轉成 `Chunk`（`app/agent/state.py:10-17`），欄位為 `chunk_id` / `content` / `source` / `score` / `metadata`。

### Legacy LCEL path

`app/rag/pipelines.py:19-32`，給 `POST /api/v1/knowledgeBases/query` 用。
直接叫 `get_collection_retriever()`，沒有 hybrid、沒有 rerank、model 寫死 `gemini-3.5-flash`。

---

## Ingestion 端的 metadata 契約

`app/api/v1/endpoints/knowledgeBases.py` 在寫入前組出三個 key：

```python
metadata = {
    "knowledge_base_id": str(knowledge_base_id),
    "user_id": str(current_user.id),
    "tenant_id": str(current_user.tenant_id),
}
```

三個值都必須是 `str`——型別錯 Chroma 不會 raise，只會靜默回 0 筆。見 `docs/id-type-convention.md`。

---

## 已知缺陷

### 1. `EmbeddingFactory` 判斷式反了，永遠回 `None`

`app/rag/embeddings/embedding_factory.py:12-13`：

```python
if settings.EMBEDDING_MODEL:
    return None
```

`EMBEDDING_MODEL` 預設就是 `"gemini-embedding-001"`（`app/core/config.py:15`），永遠 truthy，
所以這個 function **永遠回 `None`**，後面的 Gemini 分支是死碼。
Chroma 拿到 `embedding_function=None` 會退回自己的預設 embedding，等於 `EMBEDDING_MODEL` 設定完全沒生效。

### 2. `retrieval_execution` 讀了不存在的欄位

`app/agent/nodes/retrieval.py:18`：

```python
chunks = [Chunk(chunk_id=doc.id, content=doc.content, metadata=doc.metadata) for doc in docs]
```

上游是 LangChain `Document`，內容欄位叫 `page_content` 不是 `content`。這行會 `AttributeError`。

### 3. `ChromaHopRetriever` 完全繞過 vector store 層

`app/routing/branches/multi_hop.py:88-107` 自己走一套獨立的路：

- 用 `chromadb.Client()`——**in-memory client，不是 `PersistentClient`**
- collection 名寫死 `kb_{knowledge_base_id}`，跟 `Collection_manager` 的命名規則無關
- `where` 只過濾 `knowledge_base_id` 一層，不符合三層 filter 的租戶隔離規則

結果是 multi-hop 分支讀的是一個空的、不同的資料庫。

---

## 附註：過期的文件

`vectorstore-collection-partitioning.md` 結尾提到「`retrieval_execution` 忘了 `await`」的 bug——
那個已經修好了（`retrieval.py:10` 現在是 `async def`，`:12` 有 `await`），該段附註已過期。
