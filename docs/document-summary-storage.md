# 文件摘要存哪裡：SQL 欄位 vs Vector store

適用於 GLOBAL 分支（`app/routing/branches/global_summary.py`）要讀的「每份文件一段摘要」。這份摘要在 ingestion 時產生一次，之後每次 GLOBAL 查詢只讀不寫。

## 規則

**摘要是文件層級的屬性，存在 `documents` 表的欄位，不進 vector store。**

判準：**存取樣式是「用 kb_id 撈全部」還是「用語意找相似」？** 前者是 key 查詢，屬於關聯式資料庫；只有後者才該進向量庫。

```
ingestion（一次）
  documents.raw_text ──LLM──► documents.summary

GLOBAL 查詢（每次）
  SELECT summary WHERE tenant/user/kb AND status='indexed'
        └─► map-reduce ─► 答案
```

---

## 對照

| | **SQL：`documents.summary`** | **Vector store：存進 Chroma** |
|---|---|---|
| 讀取 | `SELECT` 一次 indexed 查詢 | `collection.get(where=…)` 全掃 collection metadata |
| 筆數 | 一份文件一列 | 每 chunk 重複一份，或另建一筆摘要記錄 |
| 產生時機 | 任意 —— 先寫 chunk 回應，之後 UPDATE | **必須早於 `add_documents`**，否則要事後 update N 筆 |
| 對既有 filter 影響 | 無 | 三處 filter 都要加 `kind` 判別 |
| 刪除文件 | 跟著 row 消失 | 要另外刪 |
| 更新摘要 | 一次 UPDATE | 1 或 N 次 update，且需要 chunk id |
| Embedding 成本 | 無 | 每份文件多一次，但從不做相似度搜尋 |
| 前置工作 | 一次 migration | 長期的 filter 紀律 |

---

## 決定性的三點

### 1. 存進 Chroma 會把「產生時機」寫死

Chroma 的 metadata 在插入當下就固化（`app/vectorstore/StoreIndexer.py:13`）：

```python
Document(page_content=doc.page_content, metadata=metadata | doc.metadata)
```

要讓 `summary` 出現在裡面，它必須在 `add_documents` **之前**就存在 —— 也就是 upload 請求裡同步跑一次「讀全文 → LLM」，使用者得等著。想改成背景產生，就只剩「事後回頭 update N 筆 chunk」這條路，而目前 `add_documents`（`app/api/v1/endpoints/knowledgeBases.py:89`）連 chunk id 都沒有接。

SQL 欄位沒有這個約束：chunk 先寫、立刻回應，摘要晚點 UPDATE 那一列即可。`IngestionStatus` 的 `PROCESSING` / `INDEXED`（`app/models/document.py:13-17`）正是為了表達這個中間態而存在，現在完全沒被用到 —— upload 是同步跑完直接寫 `INDEXED`（`knowledgeBases.py:117`）。

### 2. 存取樣式對不上向量庫

GLOBAL 要的是「給我這個 kb 的全部摘要」，這是 key 查詢，不是相似度檢索。**注意 `global_summary.py` 從頭到尾沒有 embed 過 query** —— 它呼叫的是 `collection.get(where=…)`，不是 `.query()`。向量完全沒派上用場。

走 Chroma 的話，50 份摘要要掃過 5000 筆 chunk 記錄才湊得齊，而且現在 chunk metadata 裡**沒有 `document_id`**，連去重的依據都得先補上。

`documents` 表的 `knowledge_base_id` 和 `user_id` 都已經有 index（`app/models/document.py:32-41`），這正是關聯式資料庫的本行。

### 3. 摘要會靜默污染檢索

放進同一個 collection 的話，`get_collection_retriever` 目前的條件是 `$and[tenant_id, user_id, knowledge_base_id]`（`app/rag/retriever/common.py:7-14`），**摘要記錄完全符合** → LOOKUP 檢索會把摘要當成一般 chunk 撈出來。`get_all_documents_in_kb`（BM25 那一半，`StoreIndexer.py:19-34`）同理。

要防就得在三個地方都補 `{"kind": "chunk"}` 判別：

```
get_collection_retriever    + kind=chunk
get_all_documents_in_kb     + kind=chunk
fetch_document_summaries    + kind=summary
```

漏掉任何一處都是**靜默污染**，沒有錯誤訊息 —— 跟 `docs/id-type-convention.md` 講的 UUID 進 `where` filter 是同一類最難追的 bug。SQL 方案動不到任何既有 filter。

---

## 資料歸屬

摘要跟 `filename`、`page_count`、`raw_text` 同一層，都是**文件層級**的屬性，而 `documents` 表已經有那一列了。`raw_text`（`app/models/document.py:67`）就躺在旁邊，產摘要時直接讀它，不必回 storage 重讀檔。

Chroma 存的是 **chunk 層級**的東西。把文件層級的屬性塞進 chunk 存放區，等於為了省一個 nullable 欄位，而在另一個 store 裡複製一份文件層級的記錄。

---

## 唯一支持 vector store 的理由，以及它為什麼不夠

「不想跑 migration」—— `migrations/` 目錄目前不存在（且被 `.gitignore` 排除），`app/main.py` 也沒有 `create_all`，所以加欄位確實有一次性摩擦。

但這是**一次性**的（加 `users.tenant_id` 時已經走過一遍），而 filter 紀律是**每次改檢索都要重新記得**的長期負擔。用長期負擔換一次性摩擦不划算。

---

## 落地形狀

```python
# app/models/document.py
tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
summary:   Mapped[Optional[str]] = mapped_column(Text, nullable=True)   # null = 還沒算好
```

`tenant_id` 是 `0a3180c` 的遺留缺口 —— 當時只加了 `users.tenant_id`，走 SQL 撈摘要需要它才能對齊三層邊界（見 `docs/vectorstore-collection-partitioning.md`）。

`global_summary.py:28` 的 `SummaryIndexClient` Protocol **保留** —— 它存在的目的就是讓儲存後端可抽換，換成 Postgres 實作正是它發揮作用的時候。`app/routing/branches/metadata.py:21` 已有 routing 層直接用 `AsyncSessionLocal` 的先例。

查詢條件：`tenant_id` + `user_id` + `knowledge_base_id` + `status == INDEXED` + `summary IS NOT NULL` + `deleted_at IS NULL`。最後三個條件讓還在處理中的文件自然被跳過。

---

## 檢查清單（之後要放新東西進 vector store 時）

- 我會對它做相似度搜尋嗎？→ 不會的話就不該進 Chroma。
- 它是 chunk 層級還是文件層級的屬性？→ 文件層級的放 `documents` 表。
- 它需要在 chunk 寫入之後才算得出來嗎？→ 是的話 Chroma metadata 放不進去（插入時固化）。
- 我在同一個 collection 裡放了第二種記錄嗎？→ 所有既有 filter 都要補判別鍵，漏一處就是靜默污染。
