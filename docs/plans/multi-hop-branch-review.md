# Code review：`app/routing/branches/multi_hop.py`

> **狀態：僅 review，尚未修改任何程式碼。** 下面「建議的修法」是提案，不是已完成的變更。
> Review 日期：2026-09-07。
> 同日補充：P1-2（`HybridRetriever` 阻塞 event loop）與文末附錄（`get_retriever` 該不該是 `async`）。

## Context

Review 對象是 `app/routing/branches/multi_hop.py`（MULTI_HOP 分支，唯一的 agentic 分支）。

這個檔案剛從舊版重構過——舊版自己開 `chromadb.Client()`（in-memory）繞過整個 vector store 層，
新版改成注入 `Runnable` retriever，方向正確。但重構過程中留下三個會讓程式跑不起來的 bug，
以及一個會造成**跨租戶資料外洩**的共享狀態問題。

現況：這個 branch 從 import 到執行，沒有任何一條路徑能跑通。

---

## 發現清單（依嚴重度排序）

### [x] P0-1｜`self.retriever` 這個屬性不存在 → `AttributeError`

**→ Bucket B**（讓 `multi_hop` 能跑）

`multi_hop.py:118` 建構子寫的是 `self._retriever`（底線開頭），
但 `multi_hop.py:131-132` 讀寫的是 `self.retriever`（無底線）：

```python
self._retriever = retriever              # line 118

if not self.retriever:                   # line 131 → AttributeError
    self.retriever = BasicRetriever()... # line 132 → 建出一個全新的、沒人讀的屬性
```

`MultiHopBranch` 只有 `retrieve` 這個 method，沒有 `retriever` 屬性，
所以 `__call__` 第一行就炸。就算改成賦值先行，`retrieve()`（`:125`）讀的仍是
`self._retriever`，永遠是 `None`。**兩邊名字對不上，讀寫的是兩個不同的東西。**

---
### [x] P0-2｜`get_retriever()` 沒有 `await` → 拿到 coroutine 而不是 Runnable

**→ Bucket B**（讓 `multi_hop` 能跑）；拿掉 `async` 本身屬 **Bucket F-3**

`multi_hop.py:132`：

```python
self.retriever = BasicRetriever().get_retriever(...)   # 缺 await
```

`BasicRetriever.get_retriever` 是 `async def`（`app/rag/retriever/basic_retriever.py:8`），
不 `await` 拿到的是 coroutine object，接著 `:125` 對它呼叫 `.ainvoke()` 會再炸一次。

> 這是同一類 bug 的第二次出現——`docs/vectorstore/vectorstore-collection-partitioning.md`
> 結尾記過 `app/agent/nodes/retrieval.py` 的同款漏 `await`（那個已修）。

> **「那乾脆把 `async` 刪掉？」** 這個問題值得認真回答——`get_retriever` 只是組裝 pipeline，
> 不是真的 invoke，兩個實作的 body 裡也確實一個 `await` 都沒有。
> 結論是**現在先補 `await`、之後再拿掉 `async`**，完整推導見〈附錄〉。


## 附錄：`get_retriever` 到底需不需要 `async`？

P0-2 的修法是「補上 `await`」。但更根本的問題是：**既然 `get_retriever` 只是組裝一條
pipeline、不是真的 invoke，它為什麼要是 `async`？** 這個質疑是對的，值得記錄完整推導。

### 現況：兩個實作的 body 裡一個 `await` 都沒有

| 實作 | body 做的事 | 有 `await` 嗎 |
|---|---|---|
| `BasicRetriever`（`basic_retriever.py:11`） | 一行 `get_collection_retriever(...)` → `as_retriever()` | 沒有 |
| `HybridRetriever`（`hybrid_retriever.py:15-24`） | 撈整個 KB + 建 BM25 index + 組 ensemble | 沒有 |

`async def` 沒有任何 `await`，是不折不扣的 code smell。`BasicRetriever` 更是純組裝，
`async` 對它毫無意義。

### 「多型」不是保留 async 的理由（至少現在不是）

直覺的辯護是「ABC 要求兩個子類簽名一致（`docs/runnable-or-not.md:147`），
而呼叫端拿到 `RetrieverFactory` 時不知道是哪個實作，所以不能自己決定要不要 offload」。

**但實際上沒有任何呼叫端在用多型**：

- `app/agent/nodes/retrieval.py:12` — 寫死 `HybridRetriever`
- `app/routing/branches/multi_hop.py:132` — 寫死 `BasicRetriever`

沒有一個地方把變數宣告成 `RetrieverFactory`。所以多型是設計意圖，不是現況需求，
拿它當保留 `async` 的理由並不成立。

### 真正的理由：`HybridRetriever` 的 builder 不是純 builder

`get_retriever` 對 `BasicRetriever` 而言確實是純組裝，但對 `HybridRetriever` 不是——
它在 build 階段就把整個 BM25 index 建出來了（見 P1-2）。而這是**刻意的設計**，
`docs/runnable-or-not.md:90` 寫得很清楚：lazy 形狀的整個動機就是把這筆成本
關在 `get_retriever()` 裡、只付一次，而不是每個 query 重付。

所以真正的問題不是「組裝要不要 async」，而是**這個 blocking 的重活該由誰丟到 thread**：

| offload 放哪 | 簽名 | 代價 |
|---|---|---|
| 實作內部（`asyncio.to_thread`） | 必須 `async` | `BasicRetriever` 被迫 async 陪跑 |
| 呼叫端（`await asyncio.to_thread(fn, ...)`） | 可以 `sync` | 每個呼叫端都要自己知道哪個實作貴、並記得包 |

在沒有多型的現況下，**第二條路是站得住腳的**，而且更誠實，還順帶消滅了漏 `await`
那一類 bug（已經咬過兩次：`retrieval.py`、`multi_hop.py`）。

### 結論：`async` 是症狀，不是病因

`async` 之所以看起來必要，只是因為 `HybridRetriever` 把「建 BM25 index」塞進了 builder。
而那件事本來就不該待在那裡——`docs/vectorstore/vectorstore-collection-partitioning.md`
已經記過同一個問題：

> 每個 request 都把整個 KB 的 chunk 全部載進記憶體建 BM25 index，沒有任何 cache……
> collection 一變大這裡會先於向量檢索爆掉。

**如果把 BM25 index 抽成一個有 cache 的 provider**（依 `(tenant_id, user_id, knowledge_base_id)`
快取，文件變動才失效），`get_retriever` 就真的只剩組裝，兩個實作都可以是 `sync`，
`async` 徹底不需要。

### 建議的順序（不要混在一起做）

1. **現在**：保留 `async`、補上 `await`（P0-2）。最小改動，先讓 `multi_hop.py` 能跑。
2. **接著**：`HybridRetriever` 補 `asyncio.to_thread`（P1-2）。止血，不改介面。
3. **之後**：把 BM25 的 eager build 抽成有 cache 的 provider。做完之後
   `get_retriever` 名副其實只做組裝，那時把 `async` 拿掉是水到渠成的收尾。

第 3 步會動到 `retrieval.py`、`hybrid_retriever.py` 與快取失效策略，跟修 `multi_hop.py`
的三個 P0 是兩件事。**現在為了拿掉 `async` 而先改介面，只會把 blocking 問題從實作
搬到呼叫端，而不是解決它。**

---
### [x] P0-3｜（上游）`Chunk` 的 import 是壞的，整張 routed graph 載不進來

**→ Bucket A**（解除 blocking import，先決條件）

不在本檔案，但擋在本檔案前面，所以一併記錄。

`Chunk` 目前只定義在 `app/agent/state.py:10`。`app/routing/branches/common.py`
**既沒有定義也沒有 re-export** 它，但有兩個檔案從那裡 import：

| 檔案 | 行 | 狀況 |
|---|---|---|
| `app/routing/branches/global_summary.py` | 15 | `from ...common import Chunk` → `ImportError` |
| `app/agent/nodes/retrieval.py` | 5 | 同上 → `ImportError` |
| `app/routing/branches/common.py` | 33 | `format_citations` 的 annotation 用了未定義的 `Chunk`（因 `from __future__ import annotations` 而延後求值，不會立刻炸） |

`app/agent/graphs/routed_rag.py:26` import `global_summary_node`，所以
**整張 routed graph 在 import 期就失敗**，`multi_hop` 節點根本不會被執行到。

`multi_hop.py:28` 直接從 `app.agent.state` import `Chunk`，反而是目前唯一正確的寫法。

### [x] P1-1｜module-level singleton + 屬性快取 → 跨租戶資料外洩

> 已修（2026-09-17）：retriever 改為 `__call__` 內的 local variable 往下傳，不寫回 `self`；
> 回歸測試 `tests/routing/test_multi_hop_isolation.py`。

**→ Bucket C**（stateless 化與租戶隔離，本次 review 的主目標）

這是最嚴重的設計問題。

`multi_hop.py:181` 是 module-level singleton：

```python
agentic_subgraph = MultiHopBranch()
```

`app/agent/graphs/routed_rag.py:174` 把**同一個 instance** 接進每一次 build 的 graph。
而 `get_compiled_graph()`（`app/agent/dependencies.py:13`）是 per-request 的 FastAPI dependency
——每個 request 重新 build graph，但 `agentic_subgraph` 永遠是同一個物件。

於是 `multi_hop.py:131-132` 那個「沒有就建一個存起來」的快取：

```python
if not self.retriever:
    self.retriever = BasicRetriever().get_retriever(state.tenant_id, state.user_id, knowledge_base_id, ...)
```

會把**第一個打進來的 request 的 tenant / user / knowledge_base**烘進 retriever 的
`where` filter（`app/rag/retriever/common.py:7-14`），然後**所有後續 request、所有使用者共用它**。

這直接推翻本檔案 docstring 第 11-12 行宣稱的租戶隔離：

> 租戶隔離：`knowledge_base_id` 一律用 `common.enforce_knowledge_base_id()` 從 state 強制取出，每一跳檢索都帶著它

實際上 `enforce_knowledge_base_id()` 的回傳值只有在**第一次**呼叫時真的被用到；
之後每一跳帶的都是別人的 tenant scope。

**旁證**：`_run_hops(query, knowledge_base_id)`（`:155`）收了 `knowledge_base_id`
卻在函式體內完全沒用到——只有 `__call__` 的 trace 字串用了。這個 unused parameter
正是「租戶邊界其實沒有流到檢索」的訊號。

### [ ] P1-2｜（相關檔案）`HybridRetriever` 在 event loop 上做 blocking 重活

**→ Bucket F**（檢索層效能，獨立 PR）

不在本檔案，但與 P0-2 同源，是 review 過程中順帶發現的。

`app/rag/retriever/hybrid_retriever.py:16-17` 是 `async def` 裡的兩行同步重活：

```python
documents = indexer.get_all_documents_in_kb(...)          # Chroma .get() 撈整個 KB（SQLite I/O）
bm25_retriever = BM25Retriever.from_documents(documents)  # tokenize 全部 chunk + 建倒排索引（CPU-bound）
```

兩者都沒有 `await`，等於在 event loop 裡直接跑，**期間整個 FastAPI process 停擺**。
KB 有 1000 個 chunk 時是幾百毫秒起跳，且隨 KB 線性成長。

repo 內已有正確的處理範例——`app/rag/reranker/reranker.py:75-76`：

> ONNX 推論是 CPU-bound 的同步呼叫，直接在 event loop 裡跑會卡住整個 FastAPI process。

短期修法是把重的部分包進 `asyncio.to_thread`；長期修法見〈附錄〉。

> 短期修法已完成（2026-09-17，`tests/rag/test_hybrid_retriever.py`）。長期修法（BM25 cache provider + 拿掉 `async`）尚未開始，完成後再勾選。

### [ ] P2-1｜同一輪內的 sub-query 去重失效

**→ Bucket D**（護欄正確性）

docstring 第 8 行宣稱的護欄 3：「同一輪或跨輪重複的 sub-query（正規化後）不再重新檢索」。
跨輪有效，**同一輪無效**（`:163-168`）：

```python
new_sub_queries = [q for q in sub_queries if _normalize(q) not in asked]  # asked 此時還沒更新
for sub_query in new_sub_queries:
    asked.add(_normalize(sub_query))    # 過濾已經做完了，這裡加太晚
```

decomposer 在同一次回傳 `["台北天氣", "台北 天氣"]`（正規化後相同）時，兩個都會通過過濾並各檢索一次。

### [ ] P2-2｜timeout 會丟掉所有已完成的 hop

**→ Bucket D**（護欄正確性）

`:136-141`：`asyncio.wait_for` 逾時會 cancel 掉 `_run_hops`，
局部結果隨 coroutine 一起消失，`hops` 被重設成 `[]`，使用者只拿到罐頭訊息。

已經花錢檢索回來的 chunk 全部作廢，是浪費也是體驗問題。
其他三個護欄（recursion / cost / dedup）都是「停下來但保留成果」，只有 timeout 是「全丟」——不一致。

### [ ] P2-3｜cost 護欄只算檢索字數，不算 LLM token

**→ Bucket D**（護欄正確性，待決定是否改程式）

`_estimate_cost`（`:52-54`）只累加 sub-query 長度 + chunk 內容長度。
decompose 與 synthesize 兩處 LLM 呼叫（`:80`、`:98`）完全不計入，
而它們才是真正的花費大頭。且檢查點在檢索**之後**（`:172`），無法在超支前煞停。

docstring 已經聲明這是「近似值、只當安全煞車」，所以不是錯誤，但目前的近似
漏掉了成本主體，實際約束力比看起來弱。

### [ ] P3｜品質與一致性

**→ 多數在 Bucket E**（品質收尾），逐項見下表最後一欄。

| # | 位置 | 問題 | Bucket |
|---|---|---|---|
| 1 | `:84-85, :97` | `LLMMultiHopSynthesizer.__init__` 存了 `self._model`，但 `synthesize` 永遠呼叫 `LLMFactory().get_model()`，注入點是死的——測試無法替換 model | E |
| 2 | `:65, :97` | `LLMFactory().get_model()` 先實例化再呼叫，但 `get_model` 是 `@classmethod`（`app/llm/factory.py:21`）。`global_summary.py:92` 用的是 `LLMFactory.get_model()`，兩種寫法並存 | E |
| 3 | `:124-126` | `retrieve` 的 method body 多縮排一層（8 空格），可執行但與全檔不一致 | B |
| 4 | `:28` vs `:30` | `:30` 的註解說「keeps `routing` free of a runtime import on `agent`」，但 `:28` 就是一個對 `agent` 的 runtime import，註解已與程式碼矛盾 | E |
| 5 | `:3-4` | docstring 說四項護欄「全部是建構子的必填數值型參數」，但 `:113-115` 四個都有預設值，是選填 | E |
| 6 | `:155` | `_run_hops` 的 `knowledge_base_id` 參數未使用 | C |
| 7 | — | 這個 branch 沒有任何測試；四項護欄的邊界行為（尤其 for/else 的 `recursion_limit` 判定）全靠讀 code 驗證 | G |

### 讀起來可疑但其實正確的地方

- `:161-176` 的 `for...else`：`break`（收斂）與 `return`（cost cap）都會跳過 `else`，
  只有跑滿 `recursion_limit` 才記錄護欄命中——語意是對的。
- `:126` 用 `doc.page_content`：正確（LangChain `Document` 沒有 `.content`）。
  `app/agent/nodes/retrieval.py:18` 那邊寫成 `doc.content` 才是錯的。

---

## 建議的修法

### 1. 修 `Chunk` 的 import（P0-3，先做，否則什麼都跑不了）

在 `app/routing/branches/common.py` re-export，讓兩個既有 import 站點不用改：

```python
from app.agent.state import Chunk, RoutedAgentState

__all__ = ["Chunk", "enforce_knowledge_base_id", "format_citations"]
```

順手移除 `common.py:13` 已無用的 `from pydantic import BaseModel, Field`。

> 替代做法是改掉那兩個 import 站點、讓 `common.py` 不碰 `Chunk`。
> 但 `common.py` 本來就已經 runtime import `app.agent.state`（`:15`），re-export 不增加耦合，改動面也最小。

### 2. 讓 `MultiHopBranch` 變回 stateless（P0-1 / P0-2 / P1 一起解）

核心決定：**retriever 在 `__call__` 內建一次，用參數往下傳，絕不寫回 `self`。**

理由是 `docs/runnable-or-not.md:94` 已經確認過——`BasicRetriever` 只是
`collection.as_retriever(...)` 的薄包裝，**沒有建構成本**，per-request 重建等於免費。
同時 `runnable-or-not.md:150` 要求的「multi-hop 內要重用 retriever」也滿足了：
同一個 request 的所有 hop 共用這一個。

```python
async def retrieve(self, retriever: Runnable, sub_query: str) -> list[Chunk]:
    docs = await retriever.ainvoke(sub_query)
    return [Chunk(chunk_id=doc.id, content=doc.page_content) for doc in docs]

async def __call__(self, state: RoutedAgentState) -> dict:
    knowledge_base_id = enforce_knowledge_base_id(state)
    # 注入優先（測試用），否則每個 request 依自己的 tenant scope 現建。
    # 絕不快取到 self：`agentic_subgraph` 是 module-level singleton，
    # 寫回 self 會讓第一個 request 的 tenant scope 洩漏給之後所有使用者。
    retriever = self._retriever or await BasicRetriever().get_retriever(
        state.tenant_id, state.user_id, knowledge_base_id, top_k=4
    )
    ...
    hops, guardrail_hits = await asyncio.wait_for(
        self._run_hops(retriever, query, hops), timeout=self._global_timeout_seconds
    )
```

`_run_hops` 跟著改成收 `retriever`，並移除沒用到的 `knowledge_base_id` 參數。

### 3. timeout 保留局部成果（P2-2）

把 `hops` 改成由 `__call__` 建立、傳進 `_run_hops` 就地 append，
逾時後 `except` 區塊仍讀得到已完成的 hop：

```python
hops: list[HopResult] = []
guardrail_hits: list[str] = []
try:
    await asyncio.wait_for(
        self._run_hops(retriever, query, hops, guardrail_hits),
        timeout=self._global_timeout_seconds,
    )
except asyncio.TimeoutError:
    guardrail_hits.append("global_timeout")   # hops 保留，不清空
```

### 4. 修同一輪去重（P2-1）

過濾與登記合併成一步：

```python
new_sub_queries = []
for q in sub_queries:
    key = _normalize(q)
    if key in asked:
        continue
    asked.add(key)
    new_sub_queries.append(q)
```

### 5. 收尾（P3）

- `LLMMultiHopSynthesizer.synthesize` 改用 `self._model or LLMFactory.get_model()`，讓注入點活起來
- 全檔 `LLMFactory().get_model()` → `LLMFactory.get_model()`
- 修 `retrieve` 的縮排
- 更新 `:3-4`（護欄是選填有預設值）與 `:30`（已有 runtime import）兩處與程式碼矛盾的註解
- docstring 補一句：本 branch 是 module-level singleton，**任何 per-request 狀態都不得寫回 `self`**

---

## 涉及的檔案

| 檔案 | 改動 |
|---|---|
| `app/routing/branches/common.py` | re-export `Chunk`；清掉未使用的 pydantic import |
| `app/routing/branches/multi_hop.py` | 主要修改：stateless 化、`await`、去重、timeout、註解 |
| `app/rag/retriever/hybrid_retriever.py` | P1-2：把 BM25 build 包進 `asyncio.to_thread`（可與 multi_hop 分開做） |

`app/agent/nodes/retrieval.py:5` 與 `app/routing/branches/global_summary.py:15`
在做法 1 之下**不需要改動**。

（`app/agent/nodes/retrieval.py:18` 的 `doc.content` → `doc.page_content` 是另一個既有 bug，
不在這次 review 範圍，但同屬 blocking，建議一併處理。）

---

## 驗證方式

1. **Import 能過**：`uv run python -c "from app.agent.graphs.routed_rag import RoutedGraphFactory"`
   ——目前這行會因 P0-3 失敗，修完應該安靜通過。
2. **單元測試**（目前完全沒有，建議補）：注入假的 `decomposer` / `retriever` / `synthesizer`
   （建構子三個參數就是為此存在），涵蓋四項護欄：
   - `recursion_limit`：decomposer 永遠回傳新 sub-query → 命中 `recursion_limit`，hop 數等於上限
   - `max_cost_units`：假 retriever 回傳巨大 chunk → 命中 `cost_cap` 並提早返回
   - dedup：decomposer 同一輪回傳 `["A", "A "]` → retriever 只被呼叫一次
   - timeout：假 retriever `await asyncio.sleep(...)` → 命中 `global_timeout`，
     且**已完成的 hop 仍出現在 `documents`**（這是修法 3 的回歸測試）
3. **租戶隔離回歸測試**（P1 的關鍵）：對同一個 `agentic_subgraph` instance
   連續用兩個不同 `tenant_id` / `user_id` 的 state 呼叫，斷言兩次拿到的 chunk
   分別來自各自的 KB。這個測試在修好之前必須是紅的。
4. **端到端**：`uv run uvicorn app.main:app --reload`，設 `GRAPH_STRATEGY=2`，
   打一個需要多跳的問題，檢查回應的 `trace` 有 `multi_hop: hops=N kb=...`。

---
