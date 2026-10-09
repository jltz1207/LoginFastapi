# `HybridRetriever.get_retriever` 需要是 async 嗎？

結論：**不需要，維持 sync 是對的。** `get_retriever` 只負責組裝物件，裡面沒有 I/O 可以 `await`。耗時的工作都延到 `ainvoke` 階段，而且在那裡已經是 async-safe 的。

相關檔案：
- `app/rag/retriever/hybrid_retriever.py`
- `app/rag/retriever/bm25_index.py`
- `app/rag/retriever/common.py`
- `app/agent/nodes/retrieval.py`

## Q1：`get_retriever` 有 async 的必要嗎？

### `get_retriever` 做了什麼

| 步驟 | 有沒有 I/O |
|---|---|
| `CachedBM25Retriever(...)` | 沒有，只存 ids 和 `k`，建 index 延到 invoke 時才做 |
| `get_collection_retriever(...)` | 幾乎沒有，`Chroma` 物件已經被 `collection_manager.cache` 快取 |
| `EnsembleRetriever(...)` / `RunnableLambda` | 沒有，純 Python 物件 |

`async` 的作用是讓函式在等 I/O 時把 event loop 讓出去。這裡沒有東西可以 `await`，改成 `async def` 只會多出 coroutine 的開銷，而且所有 caller 都要跟著改成 `await`，例如 `BasicRetriever` 和 `RetrieverFactory` 的介面也要一起改，沒有任何好處。

### 真正耗時的工作都在 `ainvoke` 階段

- **BM25**：`BaseRetriever` 預設的 `_aget_relevant_documents` 會用 `run_in_executor` 跑 sync 實作，所以 cache miss 時建 index 的重活在 thread 上做，不會卡住 event loop。`BM25IndexProvider` 用的是 `threading.Lock` 而不是 `asyncio.Lock`，也是因為這樣。
- **Dense**：`VectorStoreRetriever` 走 `asimilarity_search`，Chroma 的預設實作同樣丟到 executor 執行。
- **Ensemble**：`EnsembleRetriever.ainvoke` 會用 `asyncio.gather` 讓兩個 retriever 並行。

`app/agent/nodes/retrieval.py` 現在的寫法是對的：sync 組裝，`await pipeline.ainvoke(query)` 執行。

### 唯一的灰色地帶：Chroma client

`get_or_create_collection` 在 cache miss 時會建 `Chroma(client=...)`，這個 constructor 會同步呼叫 `client.get_or_create_collection`。

- 現在用的是 `PersistentClient`，這只是一次本地 SQLite 查詢，每個 collection 也只在第一次發生，可以忽略。
- 如果之後改用 **`chromadb.HttpClient`**（遠端 Chroma server），這一步會變成一次同步的網路請求，會卡住 event loop。到時候比較好的做法是把 collection 初始化移到 startup 或 executor，或改用 `AsyncHttpClient`，還是不用把 `get_retriever` 改成 async。

## Q2：如果用 sync，不會 block 嗎？

會 block，但只 block 幾十 microseconds，所以沒有影響。

### 「block」不是問題，「block 多久」才是

asyncio 只有一條 thread 跑 event loop。任何 Python code 在執行期間都會佔住這條 thread，**不管是 sync 還是 async function 都一樣**。`async def` 裡沒有 `await` 的那幾行，同樣會佔住 event loop。

```python
async def foo():
    x = build_objects()   # 這行執行時，event loop 一樣被佔住
    await something()     # 只有到這裡，event loop 才能去處理別的 request
```

所以 async 的作用不是「不 block」，而是**在等 I/O 的時候可以把 event loop 讓出去**。如果沒有 I/O 要等，就沒有東西可以讓。

### 用 timeline 看

`retrieval_execution` 處理一個 request 時大致是這樣（數字是數量級估算）：

```
get_retriever()            ~10–50 µs   ← sync，佔住 loop，但只是建幾個 Python 物件
await pipeline.ainvoke()   ~100–1000 ms
  ├─ BM25 search           (在 executor thread 上跑)
  ├─ embedding API call    (網路 I/O，loop 被釋放)
  ├─ Chroma query          (在 executor thread 上跑)
  └─ rerank                (網路 I/O，loop 被釋放)
```

`get_retriever` 佔用 event loop 的時間大約是整個 request 的萬分之一。在這段時間裡其他 request 確實要等，但只等幾十 µs，感覺不到。

如果把它改成 `async def`，裡面沒有 `await`，佔住 event loop 的時間還是一樣（甚至多一點 coroutine 的開銷），沒有改善任何東西。

### 什麼情況才真的需要擔心

會出問題的是 **sync 且耗時** 的操作直接在 event loop 上跑，例如：
- 同步的網路請求，例如 `requests.get`、`chromadb.HttpClient`
- 大量 CPU 運算，例如在 loop 上直接建 BM25 index
- 同步的 DB query

這類操作的解法是丟到 executor（`run_in_executor` / `asyncio.to_thread`），或改用原生的 async client。把它包成 `async def` 本身解決不了問題。

這也是 commit `7d4fb9d` 的設計重點：把建 BM25 index 這件耗時的工作從 `get_retriever` 移到 `CachedBM25Retriever._get_relevant_documents`，讓它在 `ainvoke` 時被丟到 executor thread 執行。這樣 `get_retriever` 只剩組裝物件，維持 sync 是安全的。

## 判斷方式

判斷一段 code 會不會卡住 event loop 時，問「它跑多久、有沒有在等 I/O」，不要問「它是不是 sync」：

| 執行時間 | 有 I/O？ | 做法 |
|---|---|---|
| µs 級 | 沒有 | sync 就好（例如 `get_retriever`） |
| ms 以上 | 有，而且有 async client | 用 `async def` + `await` |
| ms 以上 | 有，但只有 sync client，或是 CPU 運算 | `asyncio.to_thread` / executor |

原則：讓 factory 只做組裝、保持 sync，I/O 都延到 invoke 時才發生。
