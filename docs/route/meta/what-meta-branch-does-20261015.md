# META 分支是做什麼的

> 查證日期：2026-10-15。內容依閱讀 `app/routing/branches/meta.py`、`app/routing/meta_intents.py`、
> `app/routing/taxonomy.py`、`app/routing/router/rule.py` 整理。第 3、6 節的 pattern 行為已用
> `tests/routing/test_meta_branch.py` 實測（Python 3.12.13、pytest 9.1.1）；其餘為閱讀程式碼的結論。

META 是 routing 六類 route 之一，專門回答**關於這個系統本身**的問題（「你能做什麼」、「你是誰」），
不是知識庫內容，也不是一般常識。

---

## 1. 在整條流程中的位置

```mermaid
flowchart LR
    Q[使用者問題] --> R[resolve<br/>追問改寫成完整問句]
    R --> RT[route<br/>cascade: rule → embedding → LLM]
    RT --> L[LOOKUP<br/>查知識庫具體內容（預設）]
    RT --> G[GLOBAL<br/>跨文件摘要]
    RT --> MD[METADATA<br/>查文件屬性：日期、類型]
    RT --> MH[MULTI_HOP<br/>多次相依檢索]
    RT --> NR[NO_RETRIEVAL<br/>常識，LLM 直接答]
    RT --> M[META<br/>問系統本身]
```

## 2. 特點

- **不檢索**：不查知識庫，也不查資料庫。
- **不呼叫 LLM**：回答全部是寫死的模板字串，所以快、零成本，回答內容固定。
- **中英雙語**：使用者原文（`state.query`）含中文字就回中文，否則回英文；判斷問題類型則用 resolver 改寫後的 `resolved_query`。
- **`documents` 永遠是空 list**：回答不附引用來源。

## 3. `meta.py` 的運作方式

用 `app/routing/meta_intents.py` 的 `classify_meta_intent()` 對 `resolved_query` 做 regex 比對，依序選一個回覆。
rule router（`router/rule.py`）判斷「是不是 META」也是呼叫同一個函式，所以 rule 送進來的問題一定對得到模板：

```mermaid
flowchart TD
    Q[resolved_query] --> I{"IDENTITY pattern？<br/>你是誰 / 你是什麼 / 你用什麼模型 /<br/>who are you / what model are you"}
    I -->|是| ID[identity 模板]
    I -->|否| C{"CAPABILITIES pattern？<br/>你能/可以…做/提供/回答/協助 /<br/>系統…有/能/支援…功能 / 我可以問你什麼 /<br/>what can you do/help"}
    C -->|是| CAP[capabilities 模板]
    C -->|否| D["default 模板<br/>（embedding / LLM 判成 META，<br/>但 pattern 沒命中時）"]
```

最後在 `trace` 附加一行 `meta: fixed-template reply, no generation`，標記這輪走了 META。

## 4. 跟 NO_RETRIEVAL 的差別

兩者都不檢索，差在問的對象：

- 「什麼是機器學習」→ 世界知識 → NO_RETRIEVAL，LLM 生成回答。
- 「你能做什麼」→ 這個系統 → META，固定模板回答。

邊界判準寫在 `app/routing/taxonomy.py` 的 `Route` docstring。

## 5. `trace` 與為什麼 META 不再回傳它

`RoutedAgentState.trace` 是 `list[str]`，graph 裡每個節點跑完都往裡面加一行，記錄這個問題被怎麼分派：

```
resolve: query resolved to standalone question
route=LOOKUP conf=0.82
```

router 出錯降級時，還會多出 `route: router failed (<ExceptionType>: <message>), degrading to fallback_chain` 這類行。

**原本的設想**（依 `taxonomy.py` 舊 docstring 與 `app/routing/PLAN.md` 推論）：開發者發現某題答錯時，
接著在聊天中問「debug」，META 就回傳路由紀錄，不用翻 log 就能看出是不是 router 分錯類。

**2026-10-15 移除**，原因：

```mermaid
flowchart TD
    A["使用者問「debug」"] --> B{"router 會分到 META 嗎？"}
    B -->|"否：rule / embedding / LLM<br/>都沒有 trace 類 pattern"| X1[走不到 trace 路徑]
    B -->|"假設會"| C["META 讀 state.trace"]
    C --> D["只有本輪紀錄：<br/>「debug」這句本身的路由，<br/>不是上一題的"]
    C --> E["含 exception 訊息，<br/>一般使用者也看得到"]
```

而且這是 debug 功能，不是終端使用者功能。`.env.dev` 已開啟 LangSmith tracing，每次 run 的節點輸入輸出、
最終 state 的 `trace` 欄位都看得到，比聊天介面回傳的字串完整。

所以現在：

- META 只回 identity / capabilities / default 三種模板。
- `state.trace` 欄位保留，各節點照常寫入，給 LangSmith / log 使用。

## 6. 為什麼 router 和 branch 共用 pattern

修正前，rule router 判斷「這是 META」用一組 regex，`meta.py` 判斷「回哪個模板」用另一組 substring。
兩組對不上，「你可以幫我做什麼」、「這個系統有哪些功能」這類問題被正確分到 META，卻拿到 `default` 回覆。
現在兩邊都呼叫 `classify_meta_intent()`，`tests/routing/test_meta_branch.py` 也檢查 embedding 的 META 例句
不會落到 `default`。

修改 `meta_intents.py` 的 pattern 時要注意：rule 命中會以 0.95 信心直接短路 cascade，pattern 太寬會把
「這份文件提到什麼功能」這種知識庫問題誤送到 META。所以 pattern 一律錨定在「你」/「系統」，並且要遞增
`rule.py` 的 `ROUTER_VERSION`。

完整 review 紀錄見 [meta-branch-review-20261015.md](../../../plans/finished/meta-branch-review-20261015.md)。
