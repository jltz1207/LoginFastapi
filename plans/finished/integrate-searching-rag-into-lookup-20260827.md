# 把 SearchingRagGraphFactory 接成 RoutedGraphFactory 的 LOOKUP 分支

## Context

`app/routing/PLAN.md`（M-INT4）早就決定 LOOKUP 分支要重用既有的 `SearchingRagGraphFactory`（retrieve→grade→(rewrite→web_search)→generate 自我修正迴圈），不要用 `routing/branches/lookup.py` 自己另外寫的簡化版 retriever/reranker/generator（打裸 `chromadb.Client()`、寫死 `ChatGoogleGenerativeAI`）。但目前 `app/agent/graphs/routed_rag.py` 實際上還是掛著 `branches/lookup.py` 的 `standard_rag_node`，跟計畫文件矛盾；`app/agent/state.py` 裡為了承接這個整合先放的 `RoutedAgentState` 也是壞的（引用了沒 import 的 `ConversationTurn`，且欄位跟 `routed_rag.py` 自己定義的 `RAGState(TypedDict)` 對不上，目前完全沒被用到）。

這次要做的：讓 `RoutedGraphFactory` 的 `lookup` 節點變成一個**真正的 LangGraph 子圖**（`add_node("lookup", compiled_searching_rag_graph)`），保留 `SearchingRagGraphFactory`/`searching_rag.py` 底下六個 node 完全不動、可以直接被外層 graph 呼叫、token 串流跟 checkpoint 語意都自然沿用。這要求外層 state 的欄位名/型別跟 `AgentState` 對得上（LangGraph 子圖是靠 channel 名稱合併父子 state，不是靠額外的轉換函式），所以 `RoutedAgentState` 要改成真的 `class RoutedAgentState(AgentState)`，而不是現在這個獨立、壞掉的 pydantic class。

因為 `RoutedGraphFactory` 是一張圖、一份共用 state，把外層 state 從 `TypedDict` 換成 pydantic 子類別會連帶影響已經掛在同一張圖上的另外五個 leaf（`global`/`metadata`/`multi_hop`/`no_retrieval`/`meta`）——它們目前都是用 `state["x"]`/`state.get("x")` 這種 dict 存取，pydantic model 不支援這樣讀。這五支同時也踩著上次盤點過的「`from routing.xxx` 少 `app.` 前綴」的 import bug，導致 `routed_rag.py` 現在整包 import 就會炸——這個 bug 不修，就算只想動 LOOKUP 也載入不了整個模組（`routed_rag.py` 在檔案最上面就 import 了全部六個 branch）。所以這次的改動範圍必然包含：

1. **核心設計工作**（真正要想清楚的部分）：`state.py` 的 `RoutedAgentState`、`routed_rag.py` 的 `ResolverNode`/`RouterNode`/`select_branch`/graph 組裝、刪掉 `branches/lookup.py`。
2. **機械性修正**（大量但低風險，是前者的必要前提）：12 個檔案的 import 前綴、另外五個 branch 的 dict 存取改成 attribute 存取。這批不改深層邏輯（不修 `metadata.py` 的 SQL/schema 問題、不修 `global_summary.py`/`multi_hop.py` 打裸 `chromadb.Client()` 的問題——這些留給之後單獨處理，這裡只做到「圖跑得起來、其他分支不會因為 state 型別換了就直接炸」）。

## 核心設計

### 1. `app/agent/state.py` — `RoutedAgentState` 改成真正繼承 `AgentState`

```python
class RoutedAgentState(AgentState):
    resolved_query: str = ""      # QueryResolver 的輸出，供 router 分類 + trace 用
    route: str = ""
    confidence: float = 0.0
    filters: dict = {}            # 僅供 debug/trace，不作租戶邊界依據（taxonomy.py 既有規則）
    documents: list = []          # 覆寫 AgentState 的 list[Document]：非 LOOKUP 分支目前回傳
                                   # branches/common.py 的 Chunk，型別還沒統一（見下方「明確不做」）
    answer: str = ""              # 非 LOOKUP 分支寫這裡；LOOKUP 走既有 chat_messages 附加邏輯
    trace: list[str] = []
```

繼承後自動拿到 `user_id`/`knowledge_base_id`/`question`/`chat_messages`/`standalone_question`/`loop_count`/`grade`/`model_used`，欄位名跟型別跟 `AgentState` 完全一致——這是 `add_node("lookup", compiled_child_graph)` 能自動合併父子 state 的前提。

### 2. `app/agent/graphs/routed_rag.py` — 三處要重寫

- **`ResolverNode.__call__`**：從 `state.chat_messages`（`BaseMessage` 列表）轉成 `list[ConversationTurn]`（`HumanMessage`→`"user"`、`AIMessage`→`"assistant"`）餵給 `QueryResolver.resolve(state.question, history)`。輸出**同時**寫進 `resolved_query`（給 router/trace 用）**和** `standalone_question`（給 LOOKUP 子圖用）——`retrieval_execution`/`grader_execution`/`generator_execution`/`web_searcher` 都固定讀 `state.standalone_question or state.question`，只寫 `resolved_query` 的話 Resolver 等於沒接上（沒有歷史時 `QueryResolver.resolve()` 本來就會直接回傳原問句，所以 `standalone_question == question` 是安全的預設）。
- **`RouterNode.__call__`**：`RouterContext(tenant_id=str(state.user_id), knowledge_base_id=str(state.knowledge_base_id), resolved_history=[...])`，`resolved_history` 從 `state.chat_messages` 取純文字內容（純 attribute 存取，不再是 `state["tenant_id"]`/`state.get(...)`）。`trace` 用 `list(state.trace)` 起手。
- **`select_branch`**：`state.confidence`/`state.route` 改 attribute 存取。
- **`RoutedGraphFactory.build()`**：
  - `g = StateGraph(RoutedAgentState)`（原本是 `RAGState`）。
  - 移除 `from app.routing.branches.lookup import standard_rag_node`，改成 `from app.agent.graphs.searching_rag import SearchingRagGraphFactory`，`lookup` 節點註冊為：
    ```python
    g.add_node("lookup", lookup or SearchingRagGraphFactory.build(checkpointer=None))
    ```
    子圖**不帶自己的 checkpointer**——持久化交給外層（`RoutedGraphFactory.build()` 拿到的那份真正的 `AsyncPostgresSaver`），這是 LangGraph 官方建議的子圖用法：子圖只負責邏輯，checkpoint 由最外層統一管理。
  - `lookup` 建構參數的型別從「一個 callable node」變成「一個已編譯的 `CompiledStateGraph`」（測試要注入假的 LOOKUP 行為時，傳一個假的 compiled graph 或另一個 node callable 都可以，LangGraph 兩種都接受）。

### 3. `app/agent/graphs/base.py` / `searching_rag.py` — 一行型別提示

`BaseGraphFactory.build(checkpointer: BaseCheckpointSaver, ...)` 和 `SearchingRagGraphFactory.build(checkpointer: BaseCheckpointSaver, ...)` 的型別提示改成 `Optional[BaseCheckpointSaver]`，如實反映「當子圖用時會傳 `None`」這個新的合法呼叫方式。不改函式邏輯（`graph.compile(checkpointer=None)` 本來就是合法呼叫，等同不持久化）。

### 4. `app/routing/branches/lookup.py` — 刪除

按 PLAN.md 決策 2 執行：`ChromaHybridRetriever`/`PassthroughReranker`/`GeminiAnswerGenerator`/`standard_rag_node` 整支不再需要，LOOKUP 完全由 `searching_rag` 子圖取代。

## 機械性修正（前提工作，先做這批，圖才載入得起來）

**Import 路徑**：以下檔案把 `from routing.xxx import ...` / `from routing import ...` 改成 `from app.routing.xxx import ...`（純字串前綴修正，不動邏輯）：
`app/routing/taxonomy.py`、`resolver.py`、`router/base.py`、`router/rule.py`、`router/embedding.py`、`router/llm.py`、`router/cascade.py`、`router/fallback_chain.py`、`branches/common.py`、`branches/metadata.py`、`branches/multi_hop.py`、`branches/global_summary.py`。（`router/speculative.py`、`cache/semantic_cache.py` 也有一樣的 bug，但這兩支不在 LOOKUP 這條 import 鏈上，這次先跳過，之後處理其他分支/元件時再修。）

**Dict 存取 → attribute 存取**（因為外層 state 從 TypedDict 換成 pydantic）：
- `branches/common.py::enforce_knowledge_base_id(state)`：`state.get("knowledge_base_id")` → `state.knowledge_base_id`，回傳前 `str()` 轉型（欄位現在是 `UUID`，下游 Chroma filter / SQL 參數要字串）。
- `branches/{no_retrieval,meta,metadata,multi_hop,global_summary}.py`：`state.get("resolved_query", "")`／`state["resolved_query"]`／`state.get("trace", [])` 之類全部改成 `state.resolved_query`／`state.trace`。這批只換存取語法，不碰各檔案內部的業務邏輯（`metadata.py` 的 SQL/schema 問題、`multi_hop.py`/`global_summary.py` 的裸 `chromadb.Client()` 問題維持原樣，留待下次單獨處理）。

## 明確不做（避免這次範圍失控）

- 不統一 `documents` 型別（`Chunk` vs `Document`）：`RoutedAgentState.documents` 這次先鬆綁成 `list`（不設 `list[Document]`），讓 LOOKUP（`Document`）跟其他分支（`Chunk`）都能塞值，不會被 pydantic 驗證擋掉。之後要不要統一是獨立任務。
- 不修 `branches/metadata.py` 的 SQL 綁定風格/schema 白名單問題。
- 不修 `branches/{multi_hop,global_summary}.py` 打裸 `chromadb.Client()`（沒接 `Collection_manager`）的問題。
- 不動 `app/api/v1/endpoints/chats.py` 的 SSE 邏輯（PLAN M-INT8 提到的 `subgraphs=True`、非串流分支的 token 事件轉換）——這次只確保 `RoutedGraphFactory.build(...).ainvoke(...)` 能正確跑完並產生正確的最終 state，不動 `/chat` 端點；`GRAPH_STRATEGY` 預設仍是 `SEARCH`，不影響現有使用者。
- 不改 `GraphStrategy` enum 數值或 `agent/dependencies.py` 的策略對照表。

## 異動後的目錄結構

標記：`[MOD]` 內容需修改、`[DEL]` 刪除、無標記＝原樣不動。只列這次計劃會碰到的 `app/agent/`、`app/routing/`。

```
app/
├── agent/
│   ├── state.py                       [MOD] RoutedAgentState 改成真正繼承 AgentState
│   ├── graphs/
│   │   ├── base.py                    [MOD] checkpointer 型別提示改 Optional[BaseCheckpointSaver]
│   │   ├── searching_rag.py           [MOD] checkpointer 型別提示改 Optional（邏輯不動，行為不變）
│   │   └── routed_rag.py              [MOD] state 換成 RoutedAgentState；ResolverNode/RouterNode/
│   │                                         select_branch 改 attribute 存取；lookup 節點改掛
│   │                                         SearchingRagGraphFactory.build(checkpointer=None) 子圖
│   ├── nodes/                         （不動：retrieval.py / grader.py / generator.py /
│   │                                    rewriter.py / searcher.py）
│   └── edges/conditional.py           （不動）
│
└── routing/
    ├── taxonomy.py                    [MOD] import 前綴修正（routing. → app.routing.）
    ├── resolver.py                    [MOD] import 前綴修正
    ├── router/
    │   ├── base.py                    [MOD] import 前綴修正
    │   ├── rule.py                    [MOD] import 前綴修正
    │   ├── embedding.py               [MOD] import 前綴修正
    │   ├── llm.py                     [MOD] import 前綴修正
    │   ├── cascade.py                 [MOD] import 前綴修正
    │   ├── fallback_chain.py          [MOD] import 前綴修正
    │   └── speculative.py             （不動——有一樣的 import bug，但不在 LOOKUP 鏈上，跳過）
    ├── cache/
    │   ├── prompt_cache.py            （不動，本來就沒有 import 前綴問題）
    │   └── semantic_cache.py          （不動——有一樣的 import bug，但不在 LOOKUP 鏈上，跳過）
    └── branches/
        ├── common.py                  [MOD] import 前綴 + enforce_knowledge_base_id 改 attribute 存取
        ├── lookup.py                  [DEL] 被 searching_rag 子圖取代（PLAN.md 決策 2）
        ├── no_retrieval.py            [MOD] state 存取改 attribute（業務邏輯不動）
        ├── meta.py                    [MOD] state 存取改 attribute（業務邏輯不動）
        ├── metadata.py                [MOD] import 前綴 + state 存取改 attribute（SQL/schema 問題不動）
        ├── multi_hop.py               [MOD] import 前綴 + state 存取改 attribute（裸 chromadb.Client() 問題不動）
        └── global_summary.py          [MOD] import 前綴 + state 存取改 attribute（裸 chromadb.Client() 問題不動）
```

## 驗證方式

1. **靜態 import 檢查**：`uv run python -c "import app.agent.graphs.routed_rag"`，確認整條 import 鏈（routing 全部子模組）不再因為前綴問題炸掉。
2. **獨立 smoke test**（不需要真的啟動 FastAPI，也不需要真的打 Gemini API 的其餘部分）：寫一個一次性 script，用 `langgraph.checkpoint.memory.MemorySaver()` 當 checkpointer、注入一個永遠回傳 `Route.LOOKUP` 的假 router（例如 `NoOpRouter()`，`router/base.py` 已經有），組出 `RoutedGraphFactory.build(checkpointer=MemorySaver(), router=NoOpRouter())`，`ainvoke()` 一個帶真實 `user_id`/`knowledge_base_id`/`question` 的初始 state，斷言：跑完後 `chat_messages` 多了一則 `AIMessage`、`documents` 是 `list[Document]`、`grade`/`loop_count` 有被 `searching_rag` 內部迴圈更新過、`trace` 裡看得到 `resolve:`/`route=LOOKUP` 兩筆記錄。這一步會真的打 Gemini（`generate`/`grader`/`rewriter` 節點都會呼叫 `LLMFactory`）跟真的 Chroma（`retrieval_execution`），所以要在已經有資料 ingest 過的 knowledge base 上跑。
3. **回歸確認**：`GRAPH_STRATEGY` 維持預設 `2`（`SEARCH`），確認現有 `/chat`（`SearchingRagGraphFactory` 直接編譯路徑）行為完全不變——這條路徑這次唯一的改動只有 `build()` 的型別提示，不影響執行期行為。
