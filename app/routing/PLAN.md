# 把 advanced-router 整合進 AsistAi

## Context

`advanced-router`（`C:\Users\jthlee\Documents\Jason-codespace\dev\advanced-router`）是一個獨立完成的 query routing 專案：`QueryResolver → CascadeRouter(規則→embedding→LLM) → 6 類 leaf branch` 的完整 LangGraph 實作（見 `README.md`、`routing/graph.py`）。它的設計筆記原本就是在 AsistAi 情境下累積的（沿用 per-user Chroma collection + `knowledge_base_id` metadata filter 的既有模式），但程式碼是在乾淨的 sandbox 裡用假物件（`ChromaHybridRetriever` 打裸 `chromadb.Client()`、`PsycopgQueryExecutor` 開獨立連線、寫死的 `ChatGoogleGenerativeAI`）寫的，從未真正接上 AsistAi 的資料庫/向量庫/LLM 基礎設施。

AsistAi（`C:\Users\jthlee\Documents\Jason-codespace\dev\AsistAi\app`）目前只有一種問答策略：`SearchingRagGraphFactory`（`agent/graphs/searching_rag.py`）—— `retrieve→grade→(rewrite→web_search)→generate` 的單一自我修正迴圈，沒有任何 query 分類/分派層。GLOBAL 摘要、METADATA 結構化查詢、MULTI_HOP 多跳比較、系統性問題（META）目前完全沒有對應路徑，一律被硬套進同一條 retrieval 流程。

整合目的：讓 AsistAi 的 `/chat` 在 query 進檢索前先分類，把摘要/結構化查詢/多跳/常識/系統性問題導向各自的路徑，同時保留 AsistAi 現有的自我修正檢索品質（grading + web search fallback）與多模型 LLM 容錯（`LLMFactory` 的 fallback chain）。

**已與使用者確認的四項關鍵決策**（見對話中的 AskUserQuestion）：
1. **程式碼放置**：複製並改寫成 `app/` 內部模組（非 git submodule）—— `routing/`/`eval/`/`decision/`/`ops/` 整組搬進 `AsistAi/app/` 底下，直接改寫成呼叫 AsistAi 既有元件，不維護兩份程式碼。
2. **LOOKUP 分支**：重用 AsistAi 現有的 `SearchingRagGraphFactory` 子圖（retrieve→grade→rewrite→web_search→generate），不用 advanced-router 自己的簡化版 `lookup.py`。
3. **各分支生成答案的 LLM**：改用 AsistAi 的 `LLMFactory.get_model()`（含 Gemini→DeepSeek→DeepSeek fallback chain），取代 advanced-router 寫死的 `ChatGoogleGenerativeAI`。**例外**：router 分類本身（`llm.py` 的 cascade 第三層）依 `CLAUDE.md` 既有 ADR 維持只用 Gemini + prompt caching，不套用 `LLMFactory`——分類與生成是兩個不同決策範疇。
4. **GLOBAL 分支的 document summary index**：納入本次整合範圍，新建最小可用的 Celery ingestion pipeline（AsistAi 目前完全沒有 Celery app/broker，只有一個 signal-hook 檔案 `workers/celery_logging.py`）。

---

## M-INT1：程式碼落地與依賴合併

- 在 `AsistAi/app/` 下新增 `routing/`、`eval/`、`decision/`、`ops/` 四個套件，內容以 advanced-router 對應目錄為藍本複製，import path 全部改成 `app.routing.*` / `app.eval.*` 等（呼應現有 `app.agent.*`、`app.rag.*` 的慣例）。
- `advanced-router/pyproject.toml` 的依賴（`chromadb`、`langgraph`、`langchain-google-genai`、`psycopg[binary]`、`celery`）與 `AsistAi/pyproject.toml` 已有的版本相容（`langgraph>=1.2.6` vs `>=1.2.10`、`psycopg[binary]>=3.3.4` 已存在），只需新增 `celery`（AsistAi 目前完全沒裝）到 `AsistAi/pyproject.toml`；不需要 `psycopg2-binary`/`asyncpg` 之外的新 DB driver。
- advanced-router repo 本身保留不動，作為設計文件/藍圖來源（`CLAUDE.md`、`docs/concept-index.md`、`README.md` 繼續存在），但執行期程式碼以 AsistAi 內的版本為準——之後修改 routing 邏輯直接在 `AsistAi/app/routing/` 改，不回頭同步舊 repo。

## M-INT2：State 與設定橋接

- **State 合併**：新增一個 pydantic `RoutedAgentState`（`app/agent/state.py` 旁新增，或擴充現有 `AgentState`）：在既有 `AgentState` 欄位（`user_id`/`knoweledge_base_id`/`question`/`chat_messages`/`standalone_question`/`documents`/`loop_count`/`grade`/`model_used`）之外，加上 routing 需要的欄位：`resolved_query: str = ""`、`route: str = ""`、`confidence: float = 0.0`、`filters: dict = {}`、`trace: list[str] = []`。這個超集合狀態讓既有 `SearchingRagGraphFactory` 的所有節點（吃 `AgentState` 子集欄位）可以直接以「子圖」身分掛進新的外層 routing graph，不需要另外寫 state adapter/轉換函式（LangGraph 對子圖與父圖共用欄位子集的合併是原生支援的模式）。
- **RouterContext 映射**：`routing/taxonomy.py` 的 `RouterContext.tenant_id`/`knowledge_base_id` 是純 trace 用途（非租戶邊界）。映射方式：`tenant_id = str(user_id)`、`knowledge_base_id = str(knoweledge_base_id)`。真正的租戶邊界仍完全由 `enforce_knowledge_base_id()`（見 M-INT4）在每個 leaf node 內強制注入，與 `RouterContext` 無關。
- **設定合併**：把 `advanced-router` 沒有對應項的設定併入 `AsistAi/app/core/config.py` 的 `Settings`：`ROUTER_VERSION` 覆寫開關、`SEMANTIC_CACHE_THRESHOLD`（示範值 0.92，上線前需真實資料校準）、`ROUTER_ENABLED`（true→`CascadeRouter`，false→`NoOpRouter`，滿足 ablation 需求）、`GRAPH_STRATEGY` 新增 `ADAPTIVE=1`（`agent/graphs/base.py` 的 `GraphStrategy` enum 裡這個值目前未被使用，正好拿來對應新的 routing graph）。

## M-INT3：Router 層改寫（`app/routing/router/`）

- `rule.py`、`fallback_chain.py`、`cascade.py`：邏輯保持不動（純規則/組合邏輯，不依賴外部基礎設施），只改 import path。
- `embedding.py`：`GeminiEmbeddingsClient` 直接改用 AsistAi 既有的 `app/rag/embeddings/embedding_factory.py` 的 `EmbeddingFactory.get_embedding_function()`（同樣是 Gemini embedding，避免維護兩套 embedding client 設定/api key 來源）。`ROUTE_EXAMPLES` 原樣保留。
- `llm.py`：維持只用 Gemini（依 ADR，不套 `LLMFactory`），`_FIXED_PREFIX` + prompt caching 邏輯不動；`cache/prompt_cache.py` 原樣搬入 `app/routing/cache/`。
- `resolver.py`：`QueryResolver.resolve(query, history)` 保持介面不變；在新的 `ResolverNode`（見 M-INT8）呼叫端，把 `AgentState.chat_messages`（`BaseMessage` 列表）轉成 `list[ConversationTurn]`（`role`= "user"/"assistant"，`content`=訊息文字）餵給它。這一步與既有 `rewriter.py`/`condense_question_chain`（在檢索品質不佳時觸發的 rewrite）功能不同、不衝突：前者解決「這句話依賴對話歷史嗎」，後者解決「這次檢索沒找到東西，換個問法」。兩者都保留。

## M-INT4：LOOKUP = 既有 searching_rag 子圖

- **不使用** `routing/branches/lookup.py` 的 `ChromaHybridRetriever`/`PassthroughReranker`/`GeminiAnswerGenerator`。改為：把 `SearchingRagGraphFactory.build()` 內建的節點組（`retrieve_docs`→`grade_docs`→`(rewrite_question→web_searcher)`→`generate`→`tools`）直接掛進新的外層 `StateGraph` 當作一段子流程，狀態共用 M-INT2 的 `RoutedAgentState`。
- 租戶隔離：目前 `agent/nodes/retrieval.py` 的 `retrieval_execution` 已經有 `filter: {"knowledge_base_id": state.knoweledge_base_id}`，直接讀 `state.knoweledge_base_id`（而不是 router 的 `RouteDecision.filters`），現況已符合 `CLAUDE.md` 的鐵律，維持不變即可，不需額外加 `enforce_knowledge_base_id()`（AsistAi 沒有獨立 state dict，欄位本來就在 pydantic model 上，用型別保證即可）。

## M-INT5：GLOBAL 分支 + 新建 Celery summary pipeline

AsistAi 目前完全沒有 ingestion worker（`api/v1/endpoints/knowledgeBases.py` 的 `/upload` 端點是同步處理：載入→chunk→清理→直接 `store.add_documents()` 進 Chroma→存 `Document` row，全部在 request 內完成，`raw_text` 欄位已經存了整篇清理後文字）。本次新增：

1. **Celery app**：新增 `app/workers/celery_app.py`（目前只有 `celery_logging.py` 這個 signal hook，沒有 app 本體），broker/backend 用 Redis（`docker-compose.yml` 新增一個 `redis` service；`Settings` 新增 `CELERY_BROKER_URL`）。
2. **Summary task**：新增 `app/workers/tasks/summarize_document.py`，輸入 `document_id`，讀 `Document.raw_text`，用 `LLMFactory.get_model()` 生成單篇摘要，寫入 Chroma 的 `kb_{knowledge_base_id}_summaries` collection（沿用 `routing/branches/global_summary.py` 的 `ChromaSummaryIndexClient` collection 命名慣例，讓 `fetch_document_summaries()` 不用改）。
3. **觸發點**：`/upload` 端點在 `db.commit()` 成功後 `summarize_document.delay(str(new_doc.id))`，非同步排入 Celery，不擋 upload response。
4. `routing/branches/global_summary.py`：`ChromaSummaryIndexClient` 改注入 AsistAi 的 `Collection_manager`/`get_chroma_db()` 拿 client（而不是自己 `import chromadb; chromadb.Client()`），確保跟主檢索 Chroma 用同一個 persistent client/路徑。`GeminiMapReduceLLM` 改用 `LLMFactory.get_model()`（依決策 3）。

## M-INT6：METADATA 分支 — schema 對不上，需要調整白名單

`routing/branches/metadata.py` 的 `_WHITELISTED_QUERIES` 假設 `document`/`document_chunk` 表有 `author`、`document_type`、`version` 欄位；但 AsistAi 實際的 `Document` model（`app/models/document.py`）只有 `filename`/`file_extension`/`mime_type`/`created_dt`/`indexed_at`/`chunk_count` 等欄位，**沒有 `author` 欄位，也沒有獨立的 `document_chunk` 表**（chunk 內容只存在 Chroma，不落地 Postgres）。處理方式：

- 白名單縮減成 AsistAi 實際能回答的兩種：`BY_DATE_RANGE`（用 `created_dt`／`indexed_at`）、`BY_DOCUMENT_TYPE`（用 `file_extension`）。
- `BY_AUTHOR`、`BY_VERSION` 暫時移除出 `MetadataQueryType`（沒有對應欄位/表可查），記一筆待辦：「若後續需要，得先幫 `Document` 加 `author` 欄位、評估是否要真的落地 `document_chunk` 表」——不生造假資料。
- 執行層：`PsycopgQueryExecutor`（獨立開一條 psycopg 連線）改成用 AsistAi 既有的 `AsyncSessionLocal`/`get_db()`（SQLAlchemy async session）執行參數化 `select()`，不另外維護第二條到同一個 Postgres 的連線池。SQL 文字仍然全部是模組層常數，只是換成 SQLAlchemy `text()` + bind params 或 ORM `select()`，「禁止 text-to-SQL」的鐵律不變。
- `GeminiMetadataQueryExtractor` 的 structured-output 抽取邏輯不變（只是 enum 選項變少）。

## M-INT7：MULTI_HOP 分支

`routing/branches/multi_hop.py` 相對自足，主要改動：
- decomposer/synthesizer 的 LLM 呼叫改用 `LLMFactory.get_model()`（依決策 3）。
- 每一跳的檢索呼叫，複用 M-INT4 已經接好、租戶隔離正確的檢索路徑（`get_collection_retriever`），而不是 advanced-router 原本假設的抽象 retriever。
- 四項護欄（`recursion_limit`/`max_cost_units`/去重/`global_timeout_seconds`）原樣保留，數值先沿用 advanced-router 預設值（4／20000／30s），標記為示範值待真實流量校準（`CLAUDE.md` 開發約定本來就要求校準前不得當最終依據）。

## M-INT8：外層 routing graph 組裝 + `/chat` SSE 改造

- 新增 `app/agent/graphs/routed_rag.py`：`RoutedRagGraphFactory(BaseGraphFactory)`，仿照 `routing/graph.py` 的 `build_graph()` 骨架，但：
  - `resolve` 節點：`ResolverNode`，用 M-INT3 提到的轉換餵 `QueryResolver`。
  - `route` 節點：`RouterNode`，`router` 預設 `CascadeRouter()`（依 `settings.ROUTER_ENABLED` 可切換成 `NoOpRouter()`），`fallback` 預設 `FallbackChainRouter()`。
  - `select_branch`：confidence 門檻邏輯原樣保留（`CONFIDENCE_THRESHOLD=0.70`，保底 `LOOKUP`）。
  - 六個 leaf：`lookup`＝M-INT4 的既有子圖節點；`global`/`metadata`/`multi_hop`/`no_retrieval`/`meta` 為改寫後的 branch 節點。
  - checkpointer：**不用** `routing/graph.py` 的 `AsyncSqliteSaver`。直接複用 AsistAi 既有的 `app/agent/persistance/client.py` 的 `get_checkpointer()`（`AsyncPostgresSaver`，已經在 app 生命週期內管理好連線池），`RoutedRagGraphFactory.build(checkpointer=...)` 沿用現有 `get_compiled_graph()` 依賴注入模式（`agent/dependencies.py`）。
- `agent/dependencies.py` 的 `_STRATEGY_FACTORIES` 新增 `GraphStrategy.ADAPTIVE: RoutedRagGraphFactory`；`settings.GRAPH_STRATEGY` 預設**先維持 `SEARCH`（=2）不變**，上線先讓 `ADAPTIVE` 是 opt-in（改環境變數即可切換/回滾），驗證穩定後再考慮切換預設值——這是唯一的 rollout 安全網，不用額外做 feature flag 系統。
- `api/v1/endpoints/chats.py` 的 `event_stream()` 需要跟著改：
  - `STATUS_NODES` 新增 `resolve`、`route`、`global`、`metadata`、`multi_hop`、`no_retrieval`、`meta`。
  - `graph.astream_events(..., version="v2")` 改成帶 `subgraphs=True`（因為 `lookup` 現在是嵌套子圖，內部 `generate` 節點的 token 事件預設不會冒泡到外層，需要這個參數才看得到）。
  - 非 `lookup` 分支（`global`/`metadata`/`multi_hop`/`no_retrieval`/`meta`）不會有逐 token 串流（它們的 branch 是「一次性回傳 answer 字串」），需要在對應 `on_chain_end` 時把 `output["answer"]` 整段當一個 `token` 事件送出（而不是空等 `on_chat_model_stream`），否則 `full_answer`/`documents`/後續存 DB 的邏輯會拿不到值。
  - `model_used` 對這些分支要有 fallback 值（例如 `f"router:{route}"`），因為它們不像 `generate` 節點會回傳 `response_metadata.model`。

## M-INT9：觀測與 eval 工具（非阻塞主流程）

- `ops/observability.py`、`eval/metrics.py`、`eval/sample_size.py`、`eval/annotator_agreement.py`、`decision/*` 原樣搬進 `app/`，作為獨立工具/CI script，不接進 request 主路徑。
- `routing/router/fallback_chain.py` 目前用 `last_degradations` list 記降級；`RouterNode` 把這些訊息併進 `state["trace"]` 後，額外用 AsistAi 既有的 `app/core/logging.py`（structlog）把每次 route 決策/降級寫一行結構化 log（呼應 `CLAUDE.md`「Router 呼叫失敗必須走降級鏈…並把每次降級記錄進 trace」），方便之後接 `eval/metrics.py`。

## 驗證方式

1. **單元測試**：advanced-router 原有 `tests/`（`test_cascade.py`/`test_embedding.py`/`test_lookup.py` 等，共 18 支）搬進 `AsistAi` 的 `tests/` 對應調整 import path 後應該全部照跑；新增的 adapter（`RouterContext` 映射、`RoutedAgentState`、METADATA SQLAlchemy 執行器）各補 1-2 支測試，注入假物件，不打真的 Gemini/Chroma/PostgreSQL（延續 advanced-router 原本的測試風格）。
2. **手動端到端**：本機啟動 AsistAi（`uv run uvicorn main:app --reload` 或現有啟動方式），把 `GRAPH_STRATEGY=1`（`ADAPTIVE`）設進 `.env.dev`，用既有 `/chat` 端點分別丟 6 類代表性問題（可直接借用 `routing/router/embedding.py` 的 `ROUTE_EXAMPLES`），確認：
   - LOOKUP：走原本 retrieve→grade→generate 路徑，SSE 仍逐 token 串流。
   - META/NO_RETRIEVAL：零檢索，答案一次性送達。
   - METADATA：丟一個「這份文件是什麼時候上傳的」，確認查到 `created_dt`，且換一個假造的 `knowledge_base_id` 參數也查不到別的知識庫資料（租戶隔離）。
   - GLOBAL：先上傳一份文件觸發 Celery `summarize_document`，等 worker 跑完後問「這批文件在講什麼」，確認能拿到摘要。
   - 確認 `trace` 內容（透過問「debug/追蹤」觸發 META 分支的 trace 回傳）能看到完整的 resolve→route→branch 記錄。
3. **回歸**：把 `GRAPH_STRATEGY` 切回 `2`（`SEARCH`），確認舊路徑完全不受影響（新程式碼不應該改到 `SearchingRagGraphFactory`/`agent/nodes/*` 本身，只是把它們掛成子圖節點複用）。

---

## 預期整合後目錄結構

標記：`[NEW]` 全新檔案/目錄、`[MOD]` 既有檔案需修改、`[DEL]` 不搬過來（被取代/不適用）、無標記＝原樣不動。

### `AsistAi/`（整合後）

```
AsistAi/
├── docker-compose.yml                          [MOD] 新增 redis service（Celery broker）
├── pyproject.toml                              [MOD] 新增 celery 依賴
├── alembic.ini
├── app/
│   ├── agent/
│   │   ├── dependencies.py                     [MOD] _STRATEGY_FACTORIES 加 ADAPTIVE→RoutedRagGraphFactory
│   │   ├── state.py                            [MOD] 新增 RoutedAgentState（AgentState 超集合）
│   │   ├── edges/
│   │   │   └── conditional.py
│   │   ├── function/
│   │   │   └── tavily_web_search.py
│   │   ├── graphs/
│   │   │   ├── base.py                         （GraphStrategy.ADAPTIVE 原本就存在，只是啟用）
│   │   │   ├── searching_rag.py                （不動，被當子圖複用）
│   │   │   └── routed_rag.py                   [NEW] RoutedRagGraphFactory：resolve→route→6 leaf
│   │   ├── nodes/
│   │   │   ├── generator.py / grader.py / retrieval.py / rewriter.py / searcher.py   （不動）
│   │   ├── persistance/
│   │   │   ├── agent_config.py
│   │   │   └── client.py                       （checkpointer 原樣複用，不新增第二套）
│   │   └── tools/
│   │       ├── __init__.py / ask_human.py / web_search.py
│   │
│   ├── routing/                                 [NEW] 整組搬自 advanced-router，import 改 app.routing.*
│   │   ├── __init__.py
│   │   ├── taxonomy.py                          （Route/RouteDecision/RouterContext，原樣）
│   │   ├── resolver.py                          （QueryResolver，原樣，介面不變）
│   │   ├── router/
│   │   │   ├── __init__.py
│   │   │   ├── base.py                          （BaseRouter/NoOpRouter，原樣）
│   │   │   ├── rule.py                          （原樣）
│   │   │   ├── embedding.py                     [MOD] 改用 app.rag.embeddings.EmbeddingFactory
│   │   │   ├── llm.py                           （維持寫死 Gemini，依 ADR 不套 LLMFactory）
│   │   │   ├── cascade.py                       （原樣）
│   │   │   ├── fallback_chain.py                （原樣）
│   │   │   └── speculative.py                   （原樣，仍未接進 graph，同 advanced-router 現況）
│   │   ├── cache/
│   │   │   ├── __init__.py
│   │   │   ├── prompt_cache.py                  （原樣）
│   │   │   └── semantic_cache.py                （原樣，threshold 仍是示範值）
│   │   └── branches/
│   │       ├── __init__.py
│   │       ├── common.py                        [MOD] enforce_knowledge_base_id 改吃 pydantic state
│   │       ├── lookup.py                        [DEL] 不搬——LOOKUP 改用 searching_rag 子圖
│   │       ├── global_summary.py                [MOD] 改用 Collection_manager + LLMFactory
│   │       ├── metadata.py                      [MOD] 白名單縮減、psycopg→SQLAlchemy AsyncSession
│   │       ├── multi_hop.py                     [MOD] 改用 LLMFactory + 共用檢索路徑
│   │       ├── no_retrieval.py                  [MOD] 改用 LLMFactory
│   │       └── meta.py                          （原樣，純關鍵字+固定模板，無外部依賴）
│   │
│   ├── eval/                                    [NEW] 整組搬自 advanced-router，非阻塞主流程的工具
│   │   ├── __init__.py
│   │   ├── metrics.py / sample_size.py / annotator_agreement.py
│   │   ├── eval_set/
│   │   └── ci_pipeline/
│   │
│   ├── decision/                                [NEW] 整組搬自 advanced-router
│   │   ├── __init__.py
│   │   ├── cost_matrix.py / expected_cost.py / tier_tradeoff.py
│   │   └── adrs/
│   │
│   ├── ops/                                     [NEW] 整組搬自 advanced-router
│   │   ├── __init__.py
│   │   ├── observability.py                     [MOD] 接上 app.core.logging（structlog）輸出
│   │   └── tenant_isolation_tests/
│   │
│   ├── api/v1/endpoints/
│   │   ├── chats.py                             [MOD] STATUS_NODES、subgraphs=True、非串流分支的 token 事件
│   │   ├── knowledgeBases.py                    [MOD] /upload 成功後 summarize_document.delay(...)
│   │   └── users.py
│   │
│   ├── core/
│   │   ├── config.py                            [MOD] 新增 ROUTER_ENABLED/ROUTER_VERSION/
│   │   │                                              SEMANTIC_CACHE_THRESHOLD/CELERY_BROKER_URL
│   │   ├── logging.py
│   │   └── storage/
│   │
│   ├── workers/
│   │   ├── celery_app.py                        [NEW] Celery app 本體（目前完全不存在）
│   │   ├── celery_logging.py                    （不動，掛進新 celery_app 的 startup）
│   │   └── tasks/
│   │       ├── __init__.py                      [NEW]
│   │       └── summarize_document.py            [NEW] 讀 Document.raw_text → 摘要 → 寫入
│   │                                                  kb_{knowledge_base_id}_summaries collection
│   │
│   ├── db/ session.py                           （不動）
│   ├── llm/ factory.py                          （不動，被 routing 分支重用）
│   ├── models/ ...                              （不動，Document 無 schema 變更）
│   ├── rag/ ...                                 （不動，embedding_factory 被 routing/router/embedding.py 重用）
│   ├── schemas/ ...
│   ├── services/ ...
│   ├── utils/ ...
│   └── vectorstore/ ...
│
└── tests/                                       [MOD] 新增／搬入
    ├── test_cascade.py / test_embedding.py / test_llm.py / test_rule.py /
    │   test_fallback_chain.py / test_taxonomy.py / test_resolver.py /
    │   test_prompt_cache.py / test_semantic_cache.py / test_speculative.py /
    │   test_base.py / test_common.py                                         [NEW] 搬自 advanced-router/tests/
    ├── test_global_summary.py / test_metadata.py / test_multi_hop.py /
    │   test_no_retrieval.py / test_meta.py                                   [NEW]（含 M-INT5/6/7 改寫後的對應調整）
    ├── test_routed_rag_graph.py                                              [NEW] 對應 routed_rag.py 組裝
    └── test_metadata_sqlalchemy_executor.py                                  [NEW] 對應 M-INT6 執行層調整
```

### `advanced-router/`（整合後）

保留原樣，不刪除——繼續作為設計文件/藍圖來源（`CLAUDE.md`、`docs/concept-index.md`、`README.md`、原始 `routing/`/`eval/`/`decision/`/`ops/`）。之後修改路由邏輯一律直接改 `AsistAi/app/routing/` 等，不回頭同步這個 repo；兩邊程式碼預期會逐漸分岔，此 repo 的角色從「執行期程式碼」降級為「架構決策紀錄」。
