# 把 advanced-router 整合進 AsistAi

| | |
|---|---|
| Author | `LAPTOP-I592N3QF\user` |
| Created | — |
| Branches | — |
| Base commit | — |
| Sessions | — |

> **狀態（2026-10-16 對照 master `4b6e8e0` 更新）：routing graph 已組好並註冊成 `GRAPH_STRATEGY=4`（`ROUTED`），但 `/chat` 還沒改成能跑它，所以實際上還無法使用。** 各 milestone 的進度見下方〈現況總表〉。
> 以下現況來自閱讀程式碼，未啟動 app 實測。

## Context

`advanced-router`（`C:\Users\jthlee\Documents\Jason-codespace\dev\advanced-router`）是一個獨立完成的 query routing 專案：`QueryResolver → CascadeRouter(規則→embedding→LLM) → 6 類 leaf branch` 的完整 LangGraph 實作（見 `README.md`、`routing/graph.py`）。它的設計筆記原本就是在 AsistAi 情境下累積的，但程式碼是在乾淨的 sandbox 裡用假物件（`ChromaHybridRetriever` 打裸 `chromadb.Client()`、`PsycopgQueryExecutor` 開獨立連線、寫死的 `ChatGoogleGenerativeAI`）寫的，從未接上 AsistAi 的資料庫/向量庫/LLM 基礎設施。

整合前，AsistAi 只有一種問答策略：`retrieve→grade→(rewrite→web_search)→generate` 的單一自我修正迴圈，沒有任何 query 分類/分派層。這個 graph 現在叫 `LookupRagGraphFactory`（`app/agent/graphs/lookup_rag.py`，原名 `SearchingRagGraphFactory` / `searching_rag.py`）。

整合目的：讓 AsistAi 的 `/chat` 在 query 進檢索前先分類，把摘要/結構化查詢/多跳/常識/系統性問題導向各自的路徑，同時保留現有的自我修正檢索品質（grading + web search fallback）與多模型 LLM 容錯（`LLMFactory` 的 fallback chain）。

**已與使用者確認的四項關鍵決策**：
1. **程式碼放置**：複製並改寫成 `app/` 內部模組（非 git submodule），直接改寫成呼叫 AsistAi 既有元件，不維護兩份程式碼。
2. **LOOKUP 分支**：重用 AsistAi 現有的 lookup 子圖，不用 advanced-router 自己的簡化版 `lookup.py`。
3. **各分支生成答案的 LLM**：改用 AsistAi 的 `LLMFactory.get_model()`，取代 advanced-router 寫死的 `ChatGoogleGenerativeAI`。**例外**：router 分類本身（`llm.py` 的 cascade 第三層）依 advanced-router `CLAUDE.md` 的 ADR 維持只用 Gemini + prompt caching——分類與生成是兩個不同決策範疇。
4. ~~**GLOBAL 分支的 document summary index**：新建 Celery ingestion pipeline。~~
   **已改變（2026-10-14）**：不用 Celery，改成 API process 內的 background worker，summary 存在 `documents.summary` 欄位、不進 Chroma。見 M-INT5。

---

## 現況總表

| Milestone | 狀態 | 摘要 |
|---|---|---|
| M-INT1 程式碼落地 | ⚠️ 部分 | `app/routing/` 已搬入；`eval/`、`decision/`、`ops/` 沒搬（見 M-INT9） |
| M-INT2 State 與設定 | ⚠️ 部分 | `RoutedAgentState` 完成；`ROUTER_ENABLED` 等設定都沒加 |
| M-INT3 Router 層 | ⚠️ 部分 | 已搬入；`embedding.py` 沒改用 `EmbeddingFactory`；LLM 層的決定永遠被丟棄 |
| M-INT4 LOOKUP 子圖 | ✅ 完成 | `lookup` 節點就是 `LookupRagGraphFactory` 編譯出的子圖 |
| M-INT5 GLOBAL + summary | ✅ 完成（改做法） | in-process worker + `documents.summary`，不用 Celery |
| M-INT6 METADATA | ✅ 完成 | 2026-10-15 review 後修完 |
| M-INT7 MULTI_HOP | ⚠️ 部分 | 已改用 `LLMFactory` + `BasicRetriever`；review 提出的問題尚未修 |
| M-INT8 外層 graph + `/chat` | ⚠️ 部分 | graph 已組好並註冊；**`/chat` 完全沒改** |
| M-INT9 觀測與 eval | ❌ 未開始 | 工具沒搬；route 決策沒寫 structlog |

---

## M-INT1：程式碼落地與依賴合併

- ✅ `app/routing/` 已搬入：`taxonomy.py`、`resolver.py`、`meta_intents.py`、`router/`、`cache/`、`branches/`，import 都是 `app.routing.*`。
- ❌ `eval/`、`decision/`、`ops/` 沒搬，歸到 M-INT9。
- ✅ 依賴：不需要新增 `celery`（決策 4 已改變，見 M-INT5）。
- advanced-router repo 本身保留不動，作為設計文件/藍圖來源。之後修改 routing 邏輯直接在 `app/routing/` 改，不回頭同步舊 repo。

## M-INT2：State 與設定橋接

- ✅ **State**：`app/agent/state.py` 改成三層：
  - `BaseAgentState`：共用欄位（`user_id`、`tenant_id`、`knowledge_base_id`、`chat_history`、`query`、`answer`、`resolved_query`、`documents`、`model_used`、`loop_count` 等）。
  - `LookupAgentState(BaseAgentState)`：lookup 子圖用，加 `standalone_query`、`grade`。
  - `RoutedAgentState(BaseAgentState)`：外層 routing graph 用，加 `route`、`confidence`、`filters`、`trace`、`sql_documents`。
- ✅ **RouterContext 映射**：`RouterNode` 用 `tenant_id = state.tenant_id`、`knowledge_base_id = state.knowledge_base_id` 建 `RouterContext`。它們不決定能查哪些資料，只用在 trace 和 `SemanticCache` 的 cache key。真正的租戶邊界在三個地方：endpoint 的 `get_owned_knowledge_base()`（`app/services/knowledgeBaseService.py`）、vector store 的 `StoreIndexer.scope_filter()`，以及 leaf node 內的 `enforce_knowledge_base_id()`（`app/routing/branches/common.py`），都與 `RouterContext` 無關。
  > 2026-10-14 更新：原本寫的映射方式是 `tenant_id = str(user_id)`，已改成真正的 `tenant_id`，見 `plans/finished/tenant-id-fixes-20261014.md`。
- ❌ **設定合併**：`app/core/config.py` 還沒有 `ROUTER_ENABLED`、`ROUTER_VERSION`、`SEMANTIC_CACHE_THRESHOLD`。
  - 目前要關掉 router 只能在程式碼裡傳 `NoOpRouter()` 給 `RouterNode`，無法用環境變數切換。
  - `SemanticCache`（`app/routing/cache/semantic_cache.py`）已搬入，但沒有任何地方使用。
- **策略編號改變**：原計畫用 `GraphStrategy.ADAPTIVE=1`，實際改成新增 `ROUTED=4`（`app/agent/graphs/base.py`）。`ADAPTIVE=1`、`SELF=3` 目前都沒有對應的 factory。

## M-INT3：Router 層改寫（`app/routing/router/`）

- ✅ `rule.py`、`fallback_chain.py`、`cascade.py`、`base.py`、`speculative.py` 已搬入，只改 import path。`speculative.py` 仍未接進 graph。
- ❌ `embedding.py`：`GeminiEmbeddingsClient` **仍直接用** `langchain_google_genai.GoogleGenerativeAIEmbeddings(model="models/text-embedding-004")`，沒有改用 `EmbeddingFactory`（`app/rag/embeddings/embedding_factory.py`）。所以 router 的 embedding model 和 API key 來源跟主檢索是兩套設定。
- ⚠️ `llm.py`：依 ADR 維持只用 Gemini，`_FIXED_PREFIX` + prompt caching 不動。但**這一層的分類結果永遠被丟棄**，見 [cascade-llm-layer-dead-20261015.md](cascade-llm-layer-dead-20261015.md)。
- ✅ `resolver.py`：`ResolverNode` 把 `chat_history` 轉成 dialogue turns 餵給 `QueryResolver.resolve()`。
  - 注意：`LLMQueryRewriter` 寫死 `ChatGoogleGenerativeAI(model="gemini-2.5-flash")`，沒走 `LLMFactory`。它不是 branch 的答案生成，決策 3 沒有涵蓋，要不要改還沒決定。
- 跟 lookup 子圖內的 rewrite 功能不同、不衝突：resolver 解決「這句話依賴對話歷史嗎」，lookup 的 rewrite 解決「這次檢索沒找到東西，換個問法」。兩者都保留。

## M-INT4：LOOKUP = 既有 lookup 子圖

- ✅ `RoutedGraphFactory.build()` 用 `LookupRagGraphFactory.build(checkpointer=..., tools=None)` 編譯出子圖，直接 `add_node("lookup", ...)`。lookup 原有的節點完全不動。
- ✅ 租戶隔離：`app/agent/nodes/retrieval.py` 透過 `HybridRetriever((0.6, 0.4)).get_retriever(state.tenant_id, state.user_id, state.knowledge_base_id, top_k=4)` 檢索，三層 scope 直接讀 state，不讀 router 的 `RouteDecision.filters`。
- 細節見 `plans/finished/integrate-searching-rag-into-lookup-20260827.md`、`plans/finished/reusable-hybrid-retriever-and-reranker-20260827.md`。

## M-INT5：GLOBAL 分支 + document summary

**原計畫（已放棄）**：新建 Celery app + Redis broker，`summarize_document` task 把摘要寫進 Chroma 的 `kb_{knowledge_base_id}_summaries` collection。

**實際做法**（✅ 完成，2026-10-14 merge 進 master）：
1. **Summary 存 SQL**：摘要存在 `documents.summary` 欄位，不進 Chroma。理由是「用 kb_id 撈全部摘要」屬於 key 查詢，見 `docs/route/global/document-summary-storage-20261007.md`。
2. **生成**：upload 只寫入 `PENDING` row，API process 內的 background worker（`app/ingestion/worker.py`）處理文件，並在 `app/ingestion/summarizer.py` 用 `LLMFactory.get_model()` 對 `raw_text` 做 map-reduce 摘要。summary 失敗不會讓文件失敗。詳見 `plans/in-progress/background-ingestion-20261014.md`、`docs/worker/in-process-worker-20261014.md`。
3. **讀取**：`app/routing/branches/global_summary.py` 的 `fetch_document_summaries()` 用 `get_db_ctx()` 查 `documents`（tenant + user + kb、`INDEXED`、`summary IS NOT NULL`、未 soft delete），回答時用 `LLMFactory`。
4. **舊資料**：`uv run python -m scripts.backfill_document_summaries [--apply]` 幫既有的 `INDEXED` 文件補摘要。

## M-INT6：METADATA 分支 — schema 對不上，需要調整白名單

`routing/branches/metadata.py` 原本假設 `document`/`document_chunk` 表有 `author`、`document_type`、`version` 欄位；但 AsistAi 的 `Document` model（`app/models/document.py`）**沒有 `author` 欄位，也沒有獨立的 `document_chunk` 表**（chunk 內容只存在 Chroma）。處理方式：

> 2026-10-15 更新：已依下列內容實作，細節與測試結果見 [review-metadata-branch-20261015.md](../finished/review-metadata-branch-20261015.md)。

- 白名單只查 `documents` 現有欄位，共三種：
  - `BY_DATE_RANGE`：`created_dt`，半開區間（結束日期當天整天都算）。
  - `BY_DOCUMENT_TYPE`：`file_extension`。
  - `BY_VERSION`：不另建表；同一 KB 內同一 `filename` 的每次上傳視為一個版本，版本號用 `ROW_NUMBER() OVER (ORDER BY created_dt)` 算。
- `BY_AUTHOR` 移除：`Document` 沒有 author 欄位，而且所有查詢都已限定 `user_id` 為目前使用者，依作者查沒有意義。上傳 endpoint 已改成寫入 `created_by`，KB 改成多人共用時可再加回。
- 所有查詢都排除 soft delete（`deleted_at IS NULL`）。
- 執行層：`PsycopgQueryExecutor` 已移除，改用 `AsyncSessionLocal` 執行 `text()` + `:name` bind params。SQL 文字仍然全部是模組層常數，「禁止 text-to-SQL」的鐵律不變。
- extractor（`LLMMetadataQueryExtractor`，走 `LLMFactory`）輸出固定型別欄位（`start_date`、`end_date`、`file_extension`、`filename`），不再是任意 `dict`；參數不完整、extractor 或 DB 失敗時降級回應並記入 `trace`。

## M-INT7：MULTI_HOP 分支

- ✅ decomposer / synthesizer 改用 `LLMFactory`。
- ✅ 每一跳的檢索改用 `BasicRetriever`（`app/rag/retriever/basic_retriever.py`），不再打裸 `chromadb.Client()`。
- ✅ 四項護欄（`recursion_limit`、`max_cost_units`、去重、`global_timeout_seconds`）保留，數值仍是 advanced-router 的示範值，待真實流量校準。
- ⚠️ review 提出的問題（例如 `HybridRetriever` 阻塞 event loop）尚未修，見 `plans/backlog/review-multi-hop-branch-20260917.md`。

## M-INT8：外層 routing graph 組裝 + `/chat` SSE 改造

**Graph（✅ 完成）**：`app/agent/graphs/routed_rag.py` 的 `RoutedGraphFactory`（原計畫名稱是 `RoutedRagGraphFactory`）：
- `START → resolve → route → select_branch → 6 個 leaf → END`。
- `resolve`：`ResolverNode`。
- `route`：`RouterNode`，預設 `CascadeRouter()`；router 丟例外時降級走 `FallbackChainRouter()`，並把降級記進 `trace`。
- `select_branch`：`confidence < CONFIDENCE_THRESHOLD`（0.70）一律保底走 `lookup`。
- checkpointer 沿用 `get_checkpointer()`（`AsyncPostgresSaver`），沒有用 `AsyncSqliteSaver`。
- `app/agent/dependencies.py` 已註冊 `GraphStrategy.ROUTED: RoutedGraphFactory`。`settings.GRAPH_STRATEGY` 預設仍是 `2`（`SEARCH`），`ROUTED` 是 opt-in，改環境變數即可切換或回滾。

**`/chat`（❌ 未開始）**：`app/api/v1/endpoints/chats.py` 還是只為 lookup graph 寫的，所以設 `GRAPH_STRATEGY=4` 目前無法正常使用：
- [ ] 初始 state 寫死成 `LookupAgentState`，要依策略改成 `RoutedAgentState`。（推論：傳錯型別給 routed graph 的實際行為未實測。）
- [ ] `STATUS_NODES` 只有 lookup 的節點，要加上 `resolve`、`route`、`global`、`metadata`、`multi_hop`、`no_retrieval`、`meta`。
- [ ] `graph.astream_events(..., version="v2")` 要帶 `subgraphs=True`：`lookup` 是嵌套子圖，內部 `generate` 的 token 事件預設不會冒泡到外層。
- [ ] `ANSWER_NODES` 只有 `generate`、`finalize`。非 `lookup` 分支是一次性回傳 `answer`，要在對應的 `on_chain_end` 把 `output["answer"]` 當成一個 `token` 事件送出，否則 `full_answer` 是空字串、存進 DB 的也是空的（META 的情況見 [review-meta-branch-20261015.md](../finished/review-meta-branch-20261015.md)）。
- [ ] `model_used` 對這些分支要有 fallback 值（例如 `f"router:{route}"`），因為它們不像 `generate` 會回傳 `response_metadata.model`。

## M-INT9：觀測與 eval 工具（非阻塞主流程）

❌ 全部未開始：
- [ ] `ops/observability.py`、`eval/metrics.py`、`eval/sample_size.py`、`eval/annotator_agreement.py`、`decision/*` 搬進 `app/`，作為獨立工具 / CI script，不接進 request 主路徑。
- [ ] `RouterNode` 已經把降級記進 `state.trace`，但還沒用 structlog（`app/core/logging.py`）把每次 route 決策 / 降級寫成結構化 log。目前 `routed_rag.py` 和 `app/routing/router/` 都沒有任何 log 呼叫。

## 驗證方式

1. **單元測試**：
   - 現有：`tests/routing/` 有 `test_meta_branch.py`、`test_metadata_branch.py`、`test_multi_hop_isolation.py`。
   - [ ] 還缺：router 各層（cascade / rule / embedding / llm / fallback_chain）、`resolver`、`global_summary`、`no_retrieval`、`RoutedGraphFactory` 組裝。advanced-router 原有的 `tests/` 沒有搬進來。測試一律注入假物件，不打真的 Gemini/Chroma/PostgreSQL。
2. **手動端到端**（M-INT8 的 `/chat` 改完才能做）：本機設 `GRAPH_STRATEGY=4`，用 `/chat` 分別丟 6 類代表性問題（可直接借用 `app/routing/router/embedding.py` 的 `ROUTE_EXAMPLES`），確認：
   - LOOKUP：走原本 retrieve→grade→generate 路徑，SSE 仍逐 token 串流。
   - META / NO_RETRIEVAL：零檢索，答案一次性送達，且有存進 DB。
   - METADATA：問「這份文件是什麼時候上傳的」，確認查到 `created_dt`，且換一個不屬於自己的 `knowledge_base_id` 會被 `get_owned_knowledge_base()` 擋下。
   - GLOBAL：上傳一份文件，等 background worker 把它處理成 `INDEXED` 且有 summary 後，問「這批文件在講什麼」，確認拿得到摘要。
   - 在 LangSmith 檢查該次 run 最終 state 的 `trace` 欄位，確認看得到完整的 resolve→route→branch 記錄（META 分支已不回傳 trace，見 [review-meta-branch-20261015.md](../finished/review-meta-branch-20261015.md)）。
3. **回歸**：把 `GRAPH_STRATEGY` 切回 `2`（`SEARCH`），確認舊路徑完全不受影響。

---

## 實際落地的目錄結構

標記：`[TODO]` 原計畫要做但還沒做、`[DROPPED]` 原計畫的項目已放棄。其餘都已存在。

```
AsistAi/
├── app/
│   ├── agent/
│   │   ├── dependencies.py          GraphStrategy.ROUTED → RoutedGraphFactory
│   │   ├── state.py                 BaseAgentState / LookupAgentState / RoutedAgentState
│   │   └── graphs/
│   │       ├── base.py              GraphStrategy：ADAPTIVE=1、SEARCH=2、SELF=3、ROUTED=4
│   │       ├── lookup_rag.py        LookupRagGraphFactory（原 searching_rag.py），被當子圖複用
│   │       └── routed_rag.py        RoutedGraphFactory：resolve → route → 6 leaf
│   │
│   ├── routing/
│   │   ├── taxonomy.py / resolver.py / meta_intents.py
│   │   ├── router/                  base / rule / embedding / llm / cascade / fallback_chain / speculative
│   │   │                            embedding.py [TODO] 改用 EmbeddingFactory
│   │   ├── cache/                   prompt_cache / semantic_cache（semantic_cache 尚未使用）
│   │   └── branches/                common / global_summary / metadata / multi_hop / no_retrieval / meta
│   │                                （lookup.py 沒搬，LOOKUP 用 lookup_rag 子圖）
│   │
│   ├── ingestion/                   background worker + summarizer（取代原計畫的 Celery）
│   ├── workers/
│   │   ├── celery_logging.py        原有的 signal hook，目前沒有被使用
│   │   └── celery_app.py、tasks/    [DROPPED]
│   │
│   ├── api/v1/endpoints/chats.py    [TODO] 支援 RoutedAgentState、STATUS_NODES、subgraphs=True、非串流分支答案
│   ├── core/config.py               [TODO] ROUTER_ENABLED / ROUTER_VERSION / SEMANTIC_CACHE_THRESHOLD
│   │
│   ├── eval/                        [TODO] 搬自 advanced-router
│   ├── decision/                    [TODO] 搬自 advanced-router
│   └── ops/                         [TODO] 搬自 advanced-router
│
├── docker-compose.yml               不需要 redis [DROPPED]
├── pyproject.toml                   不需要 celery [DROPPED]
└── tests/routing/                   test_meta_branch / test_metadata_branch / test_multi_hop_isolation
                                     [TODO] router 各層、resolver、global / no_retrieval、routed graph 組裝
```

### `advanced-router/`

保留原樣，不刪除，繼續作為設計文件/藍圖來源（`CLAUDE.md`、`docs/concept-index.md`、`README.md`、原始 `routing/`/`eval/`/`decision/`/`ops/`）。之後修改路由邏輯一律直接改 `AsistAi/app/routing/` 等，不回頭同步這個 repo；兩邊程式碼預期會逐漸分岔，此 repo 的角色從「執行期程式碼」降級為「架構決策紀錄」。
