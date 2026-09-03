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

## 一份文件一個 summary，夠不夠應付 GLOBAL？

短答：**對「這個語料庫在講什麼」那一類剛好是對的粒度；對「聚合/計數」和「大文件裡的次要主題」那兩類結構上不可能夠。**

### 夠用的那一類

- 「這個知識庫在講什麼」
- 「所有文件的共同主題是什麼」
- 「有哪些文件跟 X 有關」

這些問的是**文件層級的主旨**，而 summary 正是那一層的表示。map-reduce 把 N 份主旨收斂成一個答案，粒度完全對得上。這應該涵蓋多數真實的 GLOBAL 查詢。

### 結構上不夠的兩類

**1. 聚合與計數** —— 「總共提到幾個客戶」「哪一年營收最高」

Summary 說的是「這份文件討論 Q3 營收」，不是那個數字。壓縮時被丟掉的東西無法在查詢時還原。**失敗方式是靜默的**：模型會拿倖存下來的內容自信地編出看似合理的答案，使用者拿不到任何「資訊已遺失」的訊號。

**2. 大文件裡的次要主題** —— 「有沒有文件提到資安」，而資安只是某份 100 頁手冊的第 7 章

一份文件涵蓋 10 個不相關主題時，單一 summary 要嘛 10 個都淺淺帶過，要嘛只留最主要的那個。無論哪種，問到第 7 個主題都會落空。

> 跨文件的細節比較（「A 方案跟 B 方案差在哪」）按 `app/routing/taxonomy.py:28-29` 的判準本來就是 MULTI_HOP 的職責，不算 GLOBAL 的缺口。

### 放大問題的因素：壓縮比極度不均

以 `app/rag/splitters/chunkers.py:13` 的 `chunk_size=500` 估，1 頁約 3000 字元：

| 文件 | 原文 | chunks | summary | 壓縮比 |
|---|---|---|---|---|
| 2 頁備忘錄 | 6,000 字元 | ~15 | ~1,500 字元 | **4 : 1** |
| 500 頁手冊 | 1,500,000 字元 | ~3,750 | ~1,500 字元 | **1000 : 1** |

兩者在 `_map_reduce`（`app/routing/branches/global_summary.py:85`）裡**權重完全相同**，所以手冊被低估約 250 倍 —— 問「這個知識庫主要在講什麼」時，備忘錄會不成比例地扭曲答案。

另外，摘要刻意寫成中性（不預設會被問什麼，才能服務所有未來問題），代價是它對任何特定問題都不是最佳化的。`_build_map_prompt`（`:34`）在查詢時重新聚焦，但只能在「壓縮後倖存的內容」上聚焦。

### 結論：照做，但知道它是分層結構的頂層

**一份文件一個 summary 是正確的 v1，不是死路。**

真正完整的做法是階層式摘要：每 N 個 chunk 產生一段 section summary，再 roll up 成 document summary。而**那個 roll-up 層就是這裡要做的 document summary** —— 之後要往下長，`_map_reduce` 完全不用改，只是餵給它的資料從「N 份文件」變成「N 個 section」，是資料變更而非程式碼變更。

在還沒有任何 GLOBAL 流量的情況下，先蓋完整階層是在猜哪個失效模式真的會咬人。

兩個現在就能做的低成本緩解：

1. **summary 長度隨文件大小縮放** —— `documents` 表已經有 `chunk_count` / `token_count`（`app/models/document.py:71-72`）和 `page_count`（`:78`），直接拿來決定目標長度，能大幅緩和上面那個 250 倍的權重失衡。
2. **prompt 明確要求列出文件涵蓋的主題清單**，而不只是寫一段散文 —— 對「多主題文件」那個缺口有實質幫助，成本是零。

---

## 檢查清單（之後要放新東西進 vector store 時）

- 我會對它做相似度搜尋嗎？→ 不會的話就不該進 Chroma。
- 它是 chunk 層級還是文件層級的屬性？→ 文件層級的放 `documents` 表。
- 它需要在 chunk 寫入之後才算得出來嗎？→ 是的話 Chroma metadata 放不進去（插入時固化）。
- 我在同一個 collection 裡放了第二種記錄嗎？→ 所有既有 filter 都要補判別鍵，漏一處就是靜默污染。
