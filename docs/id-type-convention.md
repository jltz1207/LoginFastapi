# ID 型別慣例：什麼時候用 `UUID`、什麼時候用 `str`

適用於 `user_id`、`knowledge_base_id`（`kb_id`）、`document_id` 等所有實體 ID 的型別提示。

## 規則

**`UUID` 用在信任邊界之內，`str` 用在外部系統邊界。**

```
HTTP request ─► [ API / Schema / ORM / AgentState ]  ← UUID
                            │
                            │  ◄── 接縫：str() 明確轉一次
                            ▼
              [ Chroma / BM25 / 檔案儲存 / log ]      ← str
```

## 為什麼內層用 `UUID`

**Parse, don't validate.** `UUID` 這個型別本身就是「這個值已經被驗證過是合法 UUID」的證明：

- Pydantic 在 request 進來那一刻就把 `"undefined"`、`"123"` 擋掉並回 422，不會讓爛值往下滲。
- SQLAlchemy 的 `UUID(as_uuid=True)` column（`app/models/user.py:16`、`app/models/document.py:32-41`）本來就回傳 `uuid.UUID` 物件，不是字串。

這層若改用 `str`，等於主動丟掉這個保證，之後每個函式都得自己懷疑「我拿到的真的是 UUID 嗎」，驗證邏輯會散落各處。

## 為什麼外層用 `str`

那些系統根本沒有 UUID 的概念：

- Chroma collection name 是字串（`app/vectorstore/collection_manager.py:23` 的 f-string）。
- Chroma metadata value 只接受 `str` / `int` / `float` / `bool`；`where` filter 是**字串相等比對**。
- `fileStorage._build_storage_key()` 拼的是路徑片段。
- log / trace 是純文字。

這層拿 `UUID` 物件沒有任何好處，只是逼每個呼叫點多寫一次轉型。

## 接縫為什麼一定要明確劃出來

因為**這是整個 repo 裡唯一「型別錯了不會 crash、只會靜默回錯答案」的地方**。

Ingestion 端寫進 Chroma 的 metadata 是**字串**（`app/api/v1/endpoints/knowledgeBases.py:60-62`，值來自 `Form(...)`）：

```python
metadata = {"knowledge_base_id": knowledge_base_id}   # str
```

所以 retrieval 端 `get_collection_retriever()` 裡的 `where={"knowledge_base_id": kb_id}`（`app/rag/retriever/common.py`）如果收到的是 `UUID` 物件，Chroma **不會 raise**，只會回 0 筆 —— 症狀是「RAG 突然查不到東西」，而不是一個有 stack trace 的錯誤。這種 bug 最難追。

### 對照組：碰巧安全的路徑

Collection name 那條路徑反而不會爆，因為 `f"{uuid_obj}"` 跟 `str(uuid_obj)` 結果完全相同。所以：

- `knowledgeBases.py:84` 傳 `user_id=current_user.id`（`UUID` 物件）進 `get_or_create_collection(user_id: str)`
- `knowledgeBases.py:88` 傳 `current_user.id` 進 `_build_storage_key(user_id: str, ...)`（`app/core/storage/fileStorage.py:13`）

兩處型別提示都說謊，卻沒出事。**「碰巧安全」不是設計** —— 它掩蓋了旁邊那條真的會靜默失敗的 `where` filter 路徑，也讓讀 code 的人以為混用沒差。

## 各層對照表

| 位置 | 該用 | 現況 |
|---|---|---|
| `app/models/*.py`（SQLAlchemy ORM） | `uuid.UUID` | 已符合 |
| `app/schemas/*.py`（Pydantic DTO） | `UUID` | 已符合 |
| `app/agent/state.py::AgentState` | `UUID` | 已符合 |
| `app/agent/runtime_config.py` | `UUID` | 已符合 |
| endpoint 簽名 / service 層 | `UUID` | `knowledgeBases.py:54` 仍是 `str` |
| **← 接縫：呼叫下層前 `str()` 一次 →** | | |
| `app/vectorstore/**` | `str` | 已符合 |
| `app/rag/retriever/**` | `str` | 已符合 |
| `app/routing/**` | `str` | 已符合 |
| `app/core/storage/**` | `str` | 已符合 |

結論：**目前的分佈基本上已經對了，不需要大規模改動。**

## 待修的兩處

1. **`app/api/v1/endpoints/knowledgeBases.py:54`**
   `knowledge_base_id: str = Form(...)` → `knowledge_base_id: UUID = Form(...)`，讓 FastAPI 在邊界擋掉爛值；line 61 寫 metadata 時改成明確的 `str(knowledge_base_id)`。
   否則一個亂打的 kb_id 字串會直接變成 Chroma 裡永遠查不到的孤兒資料。

2. **`app/agent/state.py:22-33` 的 `RoutedAgentState`**
   目前是獨立的 pydantic class，欄位為 `tenant_id: str` / `knowledge_base_id: str`（且引用了未 import 的 `ConversationTurn`，是壞的）。
   應改成 `class RoutedAgentState(AgentState)`，繼承回 `UUID` 型別；接縫則落在呼叫各 branch 的地方 `str()` 一次。
   → 詳見 `docs/plans/integrate-searching-rag-into-lookup.md`。

## 檢查清單（寫新 code 時）

- 這個函式打 Chroma / 檔案系統 / log 嗎？→ 參數用 `str`。
- 這個函式屬於 API / DB / graph state 嗎？→ 參數用 `UUID`。
- 我正在跨越這兩層嗎？→ 在呼叫點明確寫 `str(...)`，不要依賴 f-string 隱含轉型。
- 我把 ID 塞進 Chroma `where` filter 嗎？→ **一定**要是 `str`，型別錯不會報錯，只會靜默回 0 筆。
