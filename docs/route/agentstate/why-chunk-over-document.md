# 為什麼 `documents` 統一成 `Chunk` 而不是 `Document`

`RoutedAgentState.documents` 目前是 `list[Any]`（`app/agent/state.py:41`），三種型別流進同一個欄位：lookup 節點寫 langchain `Document`、GLOBAL / MULTI_HOP 寫 `Chunk`、METADATA 寫原始 SQL `dict`。要統一的話該選哪一個。

核心只有一個理由，其他都是附帶的。

---

## 主要理由：`Document` 的優勢在這裡是零

`Document` 唯一勝過 `Chunk` 的地方是「LangChain 原生」——可以直接餵給 retriever、compressor、chain。

但這個欄位的所有讀取端都只做同一件事：

| 讀取端 | 做什麼 |
|---|---|
| `app/agent/nodes/generator.py:14` | `doc.page_content` join 成 prompt |
| `app/agent/nodes/grader.py:11` | `doc.page_content` join 成 prompt |
| `app/agent/nodes/finalizer.py:48` | `doc.page_content` join 成 prompt |
| `app/api/v1/endpoints/chats.py:86` | `doc.page_content` join 成 sources 字串 |

**四個全部只是把文字抽出來。** rerank 在寫進 state 之前就跑完了（`app/agent/nodes/retrieval.py:14-16`），state 裡的 `documents` 是終點，不會再進任何 LangChain pipeline。

所以 `Document` 的原生優勢一次都沒被用到。它在這裡只是個「有 `page_content` 欄位的容器」——而那正是 `Chunk` 也能做的事。

---

## 一旦優勢歸零，剩下的比較就一面倒

`Document` 只有 `page_content` + 無 schema 的 `metadata`。引用需要的「這段內容從哪來」只能塞進 metadata，而**每個來源塞的 key 都不一樣**：

- `app/agent/nodes/searcher.py:13` → `{"source": url}`
- 檢索結果 → Chroma 寫入時的 metadata
- 摘要 → `{"id": doc.id}`

`chats.py` 要渲染引用，就得先判斷「這份 doc 是哪個分支產生的」才知道去讀哪個 key。**這正是統一型別要消滅的東西**——統一了型別卻沒統一語意，等於沒統一。

`Chunk` 把 `chunk_id` / `source` / `score` 提升成有名字的欄位（`app/routing/branches/common.py:19-26`），這個判斷就消失了。`format_citations()`（`common.py:44-48`）已經寫好在那裡等著用。

---

## 誠實講反方

統一成 `Document` 的成本明顯低很多：只改三個 branch 檔案，live path 一行不動；統一成 `Chunk` 要動 `retrieval.py`、`searcher.py` 和那四個讀取端。

而且 `Chunk.source` / `Chunk.score` **目前所有地方都是預設值**，沒人設定過——所以「typed 欄位」現在是空頭支票。

仍然建議 `Chunk`，理由是：那些欄位是空的屬於**待補的缺口**，而 `Document` 的 metadata 無 schema 是**結構性的**，補不了。趁 ROUTED 還沒上線改，churn 是一次性的；等上線後才發現引用渲染要分支判斷，那個複雜度會長在 API 層永遠拔不掉。

---

## 時機：為什麼是現在

現在完全沒事，因為 ROUTED 還沒上線——`chats.py:40` 寫死建構 `LookupAgentState`，`GRAPH_STRATEGY=2` 走的是 lookup 圖，`GraphStrategy.ROUTED = 4` 雖然已註冊在 `app/agent/dependencies.py:10` 但沒被選到。所以今天四個讀取端看到的永遠只有 langchain `Document`，型別分歧完全是潛伏的。

問題會在切到 ROUTED 的那一刻爆發，而且**是靜默的**：`chats.py:69` 只認得 `retrieve_docs` / `web_searcher` 兩個節點名，GLOBAL / MULTI_HOP 的輸出根本收不到，`formatted_source_str` 會是空字串 → `AsistantMessage.sources` 全部存空值，沒有任何錯誤訊息。

---

## METADATA 是真正的例外

`app/routing/branches/metadata.py:144` 的 `rows` 是 SQL 結果（`{id, title, author, created_at}`），不是「可被引用的一段內容」。硬塞進 `Chunk.content` 是謊話。

建議讓它寫 `documents: []`，跟 `no_retrieval` / `meta` 一致——`_format_rows` 已經把答案組好了，rows 只是佐證。真要把結構化結果傳回前端，另開一個欄位，不要跟引用混用。

---

## 附帶收穫

`BaseAgentState.documents` 變成 `list[Chunk]` 之後：

- `RoutedAgentState` 那個 `list[Any]` 覆寫（`state.py:41`）連同 `:36-40` 的妥協註解可以整段刪掉
- checkpointer 讀回 state 時 Pydantic 會重新開始驗證（`list[Any]` 等於關掉驗證）
- 順帶 `state.py:2` 的 `from uuid import UUID` 是死 import，欄位全是 `str`

---

## 實作上的限制

`Chunk` 住在 `app/routing/branches/common.py:19`，但 `common.py:15` 刻意用 `if TYPE_CHECKING` 讓 `routing` 在 runtime 不依賴 `agent`。所以**不能**把 `Chunk` 搬進 `agent/state.py`（會讓 `common.py` 變成 runtime import `agent`）。

反向是安全的：`agent` → `routing` 這條邊已經存在（`app/agent/graphs/routed_rag.py:26`）。最小改動是 `Chunk` 留在原地，`agent/state.py` 從 `routing.branches.common` import 它。

語意上有點怪（核心 state 型別住在 `branches/`），但比新增模組或製造 import cycle 都好。真要搬，中性的家是 `app/schemas/`，可以之後獨立處理。
