# `get_or_create_collection()` 每次呼叫的成本

> 查證日期：2026-10-09（程式碼現況）。量測數據為 2026-09-04 實測，見〈量測結果〉的環境說明。

回答「`StoreIndexer` 每個方法都重新叫一次 `get_or_create_collection()`，多呼叫幾次會不會很慢？」

**結論：**
- 量測時（無 cache）每次約 3 ms，不是瓶頸，當時判斷「先不要修」。
- 之後 `f4ca1d9`（2026-09-07）還是在 `Collection_manager` 加了 handle cache，現在重複呼叫只剩一次 dict 查詢。
- 這個 cache 原本沒有上限、key 也不隨 partition 策略收斂；2026-10-09 已改成有上限的 LRU，key 用實際 collection 名稱，見〈現況的 cache〉。

---

## 物件關係：哪些東西是 process-wide singleton

```mermaid
flowchart TD
    GVI["get_vector_store_indexer()<br/>lru_cache(maxsize=1)"] --> SI["StoreIndexer"]
    SI --> CM["Collection_manager"]
    CM --> EF["embedding_function<br/>（__init__ 建一次）"]
    CM --> CL["client<br/>= get_chroma_db().client"]
    GCD["get_chroma_db()<br/>lru_cache"] --> PC["chromadb.PersistentClient<br/>~1150 ms，一次性"]
    CL -.-> PC
    CM --> CACHE["self._cache: LRU（max 256）<br/>key = 實際 collection 名"]
    CACHE --> W1["Chroma wrapper<br/>~3.2 ms / 個（無 cache 時每次付）"]
```

因為 `StoreIndexer` 是 singleton、它持有唯一的 `Collection_manager`，所以 `PersistentClient`、embedding function、以及 `self.cache` 實際上都是 process-wide。

---

## 量測結果（2026-09-04，無 cache 時）

```
PersistentClient()        :  1149.29 ms   ← 一次性，已被 lru_cache 擋住
Chroma() 第一次（建立）    :    15.18 ms
Chroma() 重複呼叫          :     3.214 ms  每次
  └ client.get_or_create():     0.360 ms  ← 真正的 sysdb round-trip
  └ Settings()            :     2.101 ms  ← 純浪費，見下
```

環境：Python 3.12.13 / Windows、`chromadb==1.5.8`、`langchain-chroma==1.1.0`、
`PersistentClient` 對本機 SQLite、collection 已存在、重複 300 次取平均。重現腳本見文末。

### 那 3.2 ms 花在哪

真正的 DB 查詢只佔 0.36 ms，其餘 2.9 ms 是 LangChain wrapper 的開銷，
其中 **2.1 ms 是 `Settings()`**（`langchain_chroma/vectorstores.py:351`）：

```python
_settings = client_settings or Settings()   # pydantic-settings，每次重讀 env
...
if client is not None:
    self._client = client                    # ← 我們走這條，_settings 直接被丟掉
```

我們有傳 `client=`，所以那個 `Settings()` 建完**完全沒被用到**。這是 LangChain 的問題，呼叫端繞不掉——
這也是 handle cache 真正省下的東西。剩下的 0.36 ms 是 `__ensure_collection()`（`vectorstores.py:420-427`）打進 chromadb 的 sysdb（SQLite）。

```mermaid
pie showData
    title Chroma() 重複建構的 3.214 ms（2026-09-04 實測）
    "Settings()（被丟棄）" : 2.101
    "get_or_create（sysdb）" : 0.360
    "其他 wrapper 開銷" : 0.753
```

---

## 放進一個 request 的比例看

hybrid retrieval 一個 request 會呼叫兩次 `get_or_create_collection()`：dense 在 `StoreIndexer.get_dense_retriever()`（`app/vectorstore/StoreIndexer.py:44`）；
BM25 只有在 **cache miss** 時才經 `StoreIndexer.get_all_documents_in_kb()`（`StoreIndexer.py:51`）呼叫。

```mermaid
sequenceDiagram
    participant N as retrieval_execution
    participant H as HybridRetriever
    participant CM as Collection_manager
    participant B as BM25IndexProvider
    participant E as Gemini embedding API
    participant R as FlashrankReranker

    N->>H: get_retriever()（sync，純組裝）
    H->>CM: get_or_create_collection()（dense）
    CM-->>H: cache hit：dict 查詢
    N->>H: ainvoke(query)
    par sparse
        H->>B: get(tenant, user, kb)
        alt cache miss
            B->>CM: get_or_create_collection()
            B->>B: 撈整個 KB + 建 BM25（隨 KB 大小線性成長）
        end
    and dense
        H->>E: query embedding（~100–300 ms）
    end
    H-->>N: RRF 融合 → [:top_k]
    N->>R: rerank（~50–200 ms，CPU，to_thread）
```

| 項目 | 量級 |
|---|---|
| `get_or_create_collection()` | 現在是 dict 查詢（推論：微秒級，未量測）；無 cache 時 ~3 ms / 次 |
| Gemini embedding API（query 向量化） | ~100–300 ms |
| BM25 cache miss（撈 KB + 建 index） | 隨 KB 大小線性成長；hit 時只剩 BM25 打分 |
| flashrank cross-encoder rerank | ~50–200 ms |
| generator LLM | 秒級 |

> 除了 `get_or_create_collection()` 的 3 ms 是實測，其餘量級是經驗估計，未在本專案量測。

結論不變：這 3 ms 從來不是瓶頸；同一條路上真正會痛的是 BM25 全量建置，而它現在也已經有 cache（見 `vectorstore-collection-partitioning-20261009.md`〈BM25 那條路會被分區策略放大〉）。

---

## 現況的 cache（2026-10-09 起，計劃 Phase 3）

`f4ca1d9` 加的第一版 cache 是裸 `dict`，key 永遠是 `{COLLECTION_NAME}_{user_id}_{tenant_id}`，有兩個問題：沒有上限；key 和實際 collection 名稱不對齊，
策略 1 / 3 下同一個實體 collection 會被多個 key 各包一個 wrapper。Phase 3 改成下面的寫法（`app/vectorstore/collection_manager.py:39-72`）：

```python
def _collection_name(self, user_id: str, tenant_id: str) -> str:
    # 實際的 Chroma collection 名稱，同時當 cache key
    if self.partition_strategy == vector_partition_strategy.ONE_USER_ONE_COLLECTION:
        return f"{settings.COLLECTION_NAME}_{user_id}"
    if self.partition_strategy == vector_partition_strategy.ONE_TENANT_ONE_COLLECTION:
        return f"{settings.COLLECTION_NAME}_{tenant_id}"
    return settings.COLLECTION_NAME

def get_or_create_collection(self, user_id: str, tenant_id: str) -> Chroma:
    collection_name = self._collection_name(user_id, tenant_id)
    with self._lock:                      # hit：move_to_end 後回傳
        ...
    vector_store = Chroma(...)            # miss：在 lock 外建
    with self._lock:                      # 寫入，超過上限就淘汰最舊的
        ...
```

```mermaid
flowchart LR
    subgraph before["Phase 3 之前（PARTITION_METHOD=3）"]
        K1["NAME_u1_t1"] --> W1["wrapper #1"] --> C1[("NAME_t1")]
        K2["NAME_u2_t1"] --> W2["wrapper #2"] --> C1
        K3["NAME_u3_t1"] --> W3["wrapper #3"] --> C1
    end
    subgraph after["Phase 3 之後"]
        K["NAME_t1"] --> W["wrapper"] --> C2[("NAME_t1")]
    end
    before ~~~ after
```

- **有上限**：`OrderedDict` 做 LRU，`DEFAULT_MAX_COLLECTIONS = 256`（`:13`），寫法與 `BM25IndexProvider` 一致。
  `Chroma` 物件只是 handle（HNSW index 由 chromadb 的 segment manager 管），單個不大，但 key 數量會隨 user / tenant 成長，仍要有界。
- **一個實體 collection 只對應一個 handle**：cache 大小對齊 collection 數，而不是 `(user, tenant)` 組合數。三個 `Chroma(...)` 分支也合併成一個。
- **有 lock**：`get_or_create_collection()` 會從 executor thread（BM25 loader、upload 寫入）呼叫，所以用 `threading.Lock`。
  `Chroma()` 在 lock 外建構（它會打一次 sysdb）；同名的並行 miss 可能各建一個 handle，兩者等價，後寫入的留下。
- **`evict()`**（`:69-72`）：目前沒有呼叫端。快取的是 handle 不是資料，collection 被刪除後 handle 會失效；
  之後加「刪除 user / tenant」功能時，刪 collection 的路徑必須呼叫它。

驗證：`tests/vectorstore/test_collection_manager.py`（in-memory `EphemeralClient` + 假 embedding）測了三種策略的 handle 對應、LRU 淘汰順序、`evict()` 後會拿到新 handle。

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
