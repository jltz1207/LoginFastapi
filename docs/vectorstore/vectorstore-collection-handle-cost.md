# `get_or_create_collection()` 每次呼叫的成本量測

回答「`StoreIndexer` 每個方法都重新叫一次 `get_or_create_collection()`，多呼叫幾次會不會很慢？」

**結論：技術上是浪費，但每次只有約 3 ms，不是瓶頸。現階段不建議修。**

---

## 背景：為什麼會問這個

`app/vectorstore/StoreIndexer.py` 的三個方法各自開頭都寫同一行：

```python
user_collection = self.collection_manager.get_or_create_collection(user_id=user_id, tenant_id=tenant_id)
```

也就是每次呼叫都重新建構一個 LangChain `Chroma` 物件，沒有任何重用。

---

## 量測結果

```
PersistentClient()        :  1149.29 ms   ← 一次性，已被 lru_cache 擋住
Chroma() 第一次（建立）    :    15.18 ms
Chroma() 重複呼叫          :     3.214 ms  每次
  └ client.get_or_create():     0.360 ms  ← 真正的 sysdb round-trip
  └ Settings()            :     2.101 ms  ← 純浪費，見下
```

環境：Python 3.12.13 / Windows、`chromadb==1.5.8`、`langchain-chroma==1.1.0`、
`PersistentClient` 對本機 SQLite、collection 已存在、重複 300 次取平均。

---

## 貴的東西都已經被 cache 住了

真正昂貴的兩樣不會重複付費：

| 項目 | 成本 | 為何只付一次 |
|---|---|---|
| `chromadb.PersistentClient` | ~1150 ms | `get_chroma_db()` 有 `@lru_cache`（`client.py:9`） |
| `EmbeddingFactory.get_embedding_function()` | 視 provider | 在 `Collection_manager.__init__`（`collection_manager.py:16`），而 `StoreIndexer` 是 `lru_cache(maxsize=1)` singleton（`StoreIndexer.py:36`）→ 整個 process 只建一次 |

所以每次重複付的，只有 `Chroma(...)` 這個 wrapper 的建構：**約 3.2 ms**。

---

## 那 3.2 ms 花在哪

有意思的是，真正的 DB 查詢只佔 0.36 ms，其餘 2.9 ms 是 LangChain wrapper 的開銷，
其中 **2.1 ms 是 `Settings()`**（`langchain_chroma/vectorstores.py:351`）：

```python
_settings = client_settings or Settings()   # pydantic-settings，每次重讀 env
...
if client is not None:
    self._client = client                    # ← 我們走這條，_settings 直接被丟掉
```

我們有傳 `client=`（`collection_manager.py:22`），所以那個 `Settings()` 建完**完全沒被用到**。
這是 LangChain 的問題，不是呼叫端的問題，我們也繞不掉。

剩下的 0.36 ms 是 `__ensure_collection()`（`vectorstores.py:420-427`）打進 chromadb 的 sysdb（SQLite）。

---

## 放進實際比例看

hybrid retrieval 一個 request 目前會呼叫兩次——dense 在 `app/rag/retriever/common.py:6`、
BM25 在 `StoreIndexer.py:20`——合計約 6 ms。同一個 request 裡：

| 項目 | 量級 |
|---|---|
| `get_or_create_collection()` × 2 | ~6 ms |
| Gemini embedding API（query 向量化） | ~100–300 ms |
| `get_all_documents_in_kb()` + `BM25Retriever.from_documents()` | 隨 KB 大小線性成長 |
| flashrank cross-encoder rerank | ~50–200 ms |
| generator LLM | 秒級 |

**同一個檔案裡真正會痛的是 BM25**：`app/rag/retriever/hybrid_retriever.py:16-17` 每個 request
把整個 KB 的 chunk 載進記憶體重建索引，沒有任何 cache。KB 一大，那裡會遠早於這 3 ms 爆掉。
見 `vectorstore-collection-partitioning.md`〈BM25 那條路會被分區策略放大〉。

---

## 如果之後真的要修

最小改動是在 `Collection_manager` 上加 memoization：

```python
class Collection_manager:
    def __init__(self, client=None):
        ...
        self._cache: dict[str, Chroma] = {}

    def get_or_create_collection(self, user_id: str, tenant_id: str) -> Chroma:
        name = self._collection_name(user_id, tenant_id)   # 先把命名邏輯抽出來
        if name not in self._cache:
            self._cache[name] = Chroma(
                client=self.client,
                collection_name=name,
                embedding_function=self.embedding_function,
            )
        return self._cache[name]
```

兩個注意事項：

1. **要用 bounded cache。** `PARTITION_METHOD=2` 下 key 是 `user_id`，to-C 產品的使用者數無上限，
   用裸 `dict` 會無限成長——換成 `functools.lru_cache(maxsize=...)` 或 `cachetools.LRUCache`。
   `Chroma` 物件本身只是個 handle（真正的 HNSW index 由 chromadb 的 segment manager 管），
   所以快取它不會直接吃掉向量記憶體，但仍不該無界。
2. **快取的是 handle，不是資料。** collection 被外部刪除後，快取的 handle 會失效，
   `get_or_create` 的自癒行為就沒了。目前程式沒有刪除 collection 的路徑，所以還好。

**建議：先不要修。** 3 ms 換來一層 cache 失效的心智負擔不划算。
如果要動這一塊，先處理 BM25 的全量重建，那才是量級上的問題。

---

## 重現方式

```python
import time, tempfile
import chromadb
from chromadb.config import Settings
from langchain_chroma import Chroma

path = tempfile.mkdtemp(prefix="chroma_bench_")
t0 = time.perf_counter()
client = chromadb.PersistentClient(path=path)
print(f"PersistentClient()        : {(time.perf_counter()-t0)*1000:8.2f} ms")

t0 = time.perf_counter()
Chroma(client=client, collection_name="bench", embedding_function=None)
print(f"Chroma() 1st (create)     : {(time.perf_counter()-t0)*1000:8.2f} ms")

N = 300
for label, fn in [
    ("Chroma() repeat        ", lambda: Chroma(client=client, collection_name="bench", embedding_function=None)),
    ("client.get_or_create() ", lambda: client.get_or_create_collection(name="bench", embedding_function=None)),
    ("Settings() alone       ", Settings),
]:
    t0 = time.perf_counter()
    for _ in range(N):
        fn()
    print(f"{label}  : {(time.perf_counter()-t0)/N*1000:8.3f} ms each  (x{N})")
```

> `embedding_function=None` 是刻意的——這條路徑只量 handle 建構成本，不碰 embedding。
> 注意不要在這個腳本裡呼叫 `add_texts()` / `similarity_search()`：
> 沒有 embedding function 時 chromadb 會退回自己的預設 ONNX 模型並從網路下載（約 80MB），
> 量到的會是下載時間而不是建構時間。
