# Vector store 分區策略：per-tenant collection + 三層 metadata filter

適用於 `app/vectorstore/**` 與 `app/rag/retriever/**`，回答「有 tenant、user、knowledge base 三層時，Chroma collection 該怎麼切」。

## 規則

**用「需要硬隔離或需要獨立生命週期的最大單位」切 collection，比它小的一律用 metadata filter。**

```
tenant   ──►  切 collection（安全邊界、整批刪除/匯出的單位）
  user   ──►  metadata filter
    kb   ──►  metadata filter
```

推論：**collection 名字只是效能與生命週期的決策，不該是安全邊界。**

---

## 現況

Collection 名字由 `user_id` 決定（`app/vectorstore/collection_manager.py:23`）：

```python
collection_name = f'{settings.COLLECTION_NAME}_{user_id}'
```

Collection 內部再用 `kb_id` 過濾（`app/rag/retriever/common.py:7`）：

```python
where: dict = {"knowledge_base_id": knowledge_base_id}
```

而 ingestion 只寫了一個 metadata key（`app/api/v1/endpoints/knowledgeBases.py:60-62`）：

```python
metadata = {"knowledge_base_id": knowledge_base_id}
```

DB 層面**完全沒有 tenant**：`app/models/user.py` 沒有 `tenant_id` 欄位，`app/models/asistantKnowledgeBase.py:28` 只有 `user_id`。`tenant_id` 只活在 agent state 裡，`app/api/v1/endpoints/chats.py:44` 是塞 `str(current_user.id)` 當佔位。所以今天 **tenant == user**。

### 問題：分區鍵和過濾鍵被綁在一起

隔離邊界由兩個機制各管一層，而且都不完整：

| 邊界 | 靠什麼實現 | 問題 |
|---|---|---|
| user | collection 名字（字串拼接） | 讀出 chunk 後無法驗證歸屬，只能相信名字組對了 |
| kb | metadata filter | 唯一真正被 enforce 的一層 |
| tenant | 無 | — |

chunk 的 metadata 裡**沒有 `user_id`**，直接後果有三個：

1. **`PARTITION_METHOD=1`（`GLOBAL_COLLECTION`，見 `collection_manager.py:9`）下 user 隔離根本不存在** —— 全部人共用一個 collection，只靠 kb_id 過濾。kb_id 是 UUID 所以猜不到，但那是 obscurity，不是 enforced boundary。
2. 傳錯 `user_id` 會**靜默拿到空結果**（指到另一個不存在的 collection），而不是報錯。
3. 想換分區策略就得同時改 query path，因為 filter 不完整 —— 分區策略被鎖死了。

---

## 為什麼是 per-tenant

| 方案 | collection 數量 | 問題 |
|---|---|---|
| per-KB | 使用者數 × KB 數 | 成長最快；KB 是最常被建立/刪除的東西，等於一直在建立與摧毀 HNSW index |
| per-user（現況） | 使用者數 | 對 to-C 產品無上限。Chroma 每個 collection 是獨立 index，數量到幾千就會開始痛（記憶體、載入時間）。而且 user 不是計費/合規邊界 |
| **per-tenant** | 付費組織數 | **有界**；而且它就是真正需要「整包刪掉」的單位 |

per-tenant 的另一個好處是刪除語意：GDPR 整租戶刪除 = drop collection（瞬間），而不是 scan-and-delete by metadata。

**例外**：單一 tenant 大到 filter 效能崩掉時，把**那個 tenant** 再往下切成 per-KB。所以分區策略應該是**每個 tenant 可以不同的屬性**，而不是現在 `app/core/config.py:18` 那種全域 `PARTITION_METHOD` env var。

---

## 落地步驟

### 第一步：把 filter 補完整（現在就能做，不需要 tenant table）

Ingestion 端（`app/vectorstore/StoreIndexer.py:10-13`）三個 key 都要寫：

```python
metadata = {
    "tenant_id": tenant_id,
    "user_id": user_id,
    "knowledge_base_id": kb_id,
}
```

Retrieval 端（`app/rag/retriever/common.py`）三層都要過濾：

```python
where = {"$and": [
    {"tenant_id": tenant_id},
    {"user_id": user_id},
    {"knowledge_base_id": knowledge_base_id},
]}
```

> 所有值都必須是 `str` —— 型別錯 Chroma 不會 raise，只會靜默回 0 筆。見 `docs/id-type-convention.md`。

**這一步的價值：collection 名字從此只是效能決策，不再是安全邊界。**做完之後可以隨時改分區鍵而不動 query path。

建議同時把「強制注入租戶邊界」收斂成單一守門函式，而不是各處自己拼 `where` dict。`app/routing/branches/common.py:24` 的 `enforce_knowledge_base_id()` 已經是這個 pattern：

> 「leaf node 必須自行強制注入 `knowledge_base_id`，不得信任 router 輸出的 `filters`。」

vectorstore 這層值得用同一套寫法。

### 第二步：tenant 真的落地時

1. DB 加 `users.tenant_id`、`AsistantKnowledgeBases.tenant_id`、`documents.tenant_id`（後兩者反正規化，方便過濾與稽核）。
2. `collection_manager.py:8-10` 加一個策略：

```python
class vector_partition_strategy(Enum):
    GLOBAL_COLLECTION = 1
    ONE_USER_ONE_COLLECTION = 2
    ONE_TENANT_ONE_COLLECTION = 3   # 新增
```

3. `get_or_create_collection()` 收「分區鍵」而不是寫死的 `user_id`，由策略決定傳 `tenant_id` 還是 `user_id`。
4. `chats.py:44` 的 `tenant_id=str(current_user.id)` 佔位換成真值。

**query path 完全不用改** —— 因為第一步已經讓它過濾三層了。

---

## 兩個實務細節

### 1. collection 名字要帶 embedding 版本

現在是 `{COLLECTION_NAME}_{user_id}`（`collection_manager.py:23`）。哪天改 `EMBEDDING_MODEL` 或 `EMBEDDING_PROVIDE_TYPE`（`app/core/config.py:15,19`），新舊維度的向量會混進同一個 collection，**搜尋結果靜默劣化而不會報錯**。

建議：

```python
collection_name = f'{settings.COLLECTION_NAME}_v{embed_version}_{partition_key}'
```

換模型時新舊並存，可以 tenant 逐個遷移。

### 2. BM25 那條路會被分區策略放大

`app/rag/retriever/hybrid_retriever.py:16-17` 每個 request 都把**整個 KB 的 chunk 全部載進記憶體**建 BM25 index，沒有任何 cache：

```python
documents = indexer.get_all_documents_in_kb(user_id, knowledge_base_id)
bm25_retriever = BM25Retriever.from_documents(documents)
```

現在 collection 小所以還撐得住；collection 一變大（不論是 per-tenant 還是 global）這裡會**先於向量檢索爆掉**。要嘛加 cache，要嘛把 sparse retrieval 換成 Chroma 之外的持久化 index。

---

## 檢查清單（寫新 code 時）

- 我在寫 chunk 進 Chroma 嗎？→ `tenant_id` / `user_id` / `knowledge_base_id` 三個 metadata key 一個都不能少。
- 我在組 `where` filter 嗎？→ 三層都要過濾，**不要**依賴 collection 名字當隔離。
- 我在決定 collection 名字嗎？→ 只考慮效能與生命週期，不要把它當安全邊界。
- 我在改 embedding model 嗎？→ collection 名字要換版本號，不能沿用。
- filter 的值是 `str` 嗎？→ 型別錯不會報錯，只會靜默回 0 筆。

---

## 附註：一個不相干但擋路的 bug

`RetrieverFactory.get_retriever` 是 `async def`（`app/rag/retriever/base.py:8`、`hybrid_retriever.py:12`），但 `app/agent/nodes/retrieval.py:10` 在同步函式裡直接呼叫、沒有 `await`：

```python
hybrid_retriever = HybridRetriever((0.6, 0.4)).get_retriever(...)  # coroutine, not Runnable
pipeline_rerank = with_rerank(hybrid_retriever, rerank_top_k=4)
```

拿到的是 coroutine 物件而不是 `Runnable`，接著就丟進 `with_rerank()`。這條路徑目前跑不通，動 retriever 之前要先修。
