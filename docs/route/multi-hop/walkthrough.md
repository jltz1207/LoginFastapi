
## 在流程中的位置

```
decompose → retrieve → decompose → retrieve → ...   (_run_hops)
                                                ↓
                                     hops: list[HopResult]
                                                ↓
                          synthesize(query, hops) → answer
```

`_run_hops()` 結束時手上只有一堆零散的 chunks，還沒有答案。`synthesize` 把**原始問題**和**所有 hops
的檢索結果**交給 LLM，統整出最終回答。

Multi-hop 問題的答案通常散落在不同 sub-query 的結果裡，單看任何一個 hop 都答不了原始問題，
要靠 synthesize 把推理鏈串起來。

---

## Walkthrough

### 情境

使用者的 knowledge base 裡有公司內部文件。使用者問：

> **「負責 Project Phoenix 的團隊，它所屬部門今年的預算是多少？」**

需要的三個資訊分散在不同文件：

| 需要的資訊 | 在哪份文件 |
|---|---|
| Project Phoenix 由哪個團隊負責 | 專案清單.pdf |
| 該團隊屬於哪個部門 | 組織架構.docx |
| 該部門今年預算 | 2026 預算表.md |

### Step 0：進入 `__call__`

```python
knowledge_base_id = enforce_knowledge_base_id(state)   # "kb_42"
retriever = BasicRetriever().get_retriever(tenant_id, user_id, "kb_42", top_k=4)
query = state.resolved_query
# "負責 Project Phoenix 的團隊，它所屬部門今年的預算是多少？"
```

接著進入 `_run_hops()`，外層用 `asyncio.wait_for(..., timeout=30.0)` 包住。

### Step 1：第 1 輪

`decompose`：`hops_so_far` 是空的，prompt 裡的「已查過」顯示「（尚無）」。

```python
sub_queries = await self._decomposer.decompose(query, [])
# → ["Project Phoenix 由哪個團隊負責？"]
```

`asked` 是空的，全部保留，然後 retrieve：

```python
chunks = [
    Chunk(chunk_id="proj_07", content="Project Phoenix：新一代 API Gateway，由 Platform 團隊負責，預計 Q3 上線。"),
    Chunk(chunk_id="proj_02", content="Project Atlas：資料湖遷移，由 Data 團隊負責。"),   # 不相關但被撈到
]
cost_units += _estimate_cost(...)   # 約 70
hops  = [HopResult(sub_query="Project Phoenix 由哪個團隊負責？", chunks=[...])]
asked = {"projectphoenix由哪個團隊負責？"}   # _normalize 後：小寫、去空白
```

### Step 2：第 2 輪

LLM 看得到已查過的 sub-query，判斷下一步要查團隊屬於哪個部門：

```python
# decompose → ["Platform 團隊屬於哪個部門？"]
chunks = [
    Chunk(chunk_id="org_15", content="Infrastructure 部門轄下：Platform 團隊、SRE 團隊、Network 團隊。"),
]
cost_units  # 約 110
```

### Step 3：第 3 輪（去重）

```python
sub_queries = ["Project Phoenix 由哪個團隊負責？",          # LLM 又問了一次
               "Infrastructure 部門 2026 年預算是多少？"]
```

第 1 個 sub-query 正規化後已經在 `asked` 裡，會被過濾掉，不會重新檢索：

```python
new_sub_queries = ["Infrastructure 部門 2026 年預算是多少？"]
chunks = [
    Chunk(chunk_id="budget_03", content="2026 年度預算：Infrastructure 部門 NT$ 48,000,000，較去年 +12%。"),
    Chunk(chunk_id="budget_04", content="2026 年度預算：Data 部門 NT$ 31,000,000。"),
]
cost_units  # 約 190，遠低於 20_000
```

### Step 4：第 4 輪，收斂

```python
sub_queries = await self._decomposer.decompose(query, hops)
# → []   LLM 判斷資訊已經足夠
new_sub_queries = []  →  break
```

因為是 `break` 離開，`for ... else` 的 `else` 不會執行，所以**不會**加上 `"recursion_limit"`：

```python
return hops, []   # 3 個 hops，沒有觸發任何護欄
```

### Step 5：`synthesize`

```python
answer = await self._synthesizer.synthesize(query, hops)
```

`LLMMultiHopSynthesizer` 送給 LLM 的 messages：

```
[system]
根據以下多輪檢索結果，統整回答原始問題。

[human]
Sub-query: Project Phoenix 由哪個團隊負責？
Project Phoenix：新一代 API Gateway，由 Platform 團隊負責，預計 Q3 上線。
Project Atlas：資料湖遷移，由 Data 團隊負責。

Sub-query: Platform 團隊屬於哪個部門？
Infrastructure 部門轄下：Platform 團隊、SRE 團隊、Network 團隊。

Sub-query: Infrastructure 部門 2026 年預算是多少？
2026 年度預算：Infrastructure 部門 NT$ 48,000,000，較去年 +12%。
2026 年度預算：Data 部門 NT$ 31,000,000。

原始問題：負責 Project Phoenix 的團隊，它所屬部門今年的預算是多少？
```

LLM 回覆：

```
Project Phoenix 由 Platform 團隊負責，Platform 團隊隸屬於 Infrastructure 部門，
該部門 2026 年度預算為 NT$ 48,000,000（較去年增加 12%）。
```

synthesize 在這裡做了三件事：

1. **串起推理鏈**：Phoenix → Platform → Infrastructure → 48M。這個連結在任何一個 chunk 裡都不存在。
2. **過濾雜訊**：Project Atlas、Data 部門預算這兩個 chunk 被撈到了，但會被忽略。
3. **對準原始問題作答**：回答使用者真正問的問題，不是最後一個 sub-query。只看第 3 輪的結果
   會得到「Infrastructure 預算 48M」，但使用者根本沒提過 Infrastructure。

### Step 6：回傳

```python
{
    "answer": "Project Phoenix 由 Platform 團隊負責，……NT$ 48,000,000……",
    "documents": [proj_07, proj_02, org_15, budget_03, budget_04],   # 所有 hops 攤平
    "trace": [..., "multi_hop: hops=3 kb=kb_42 guardrails=[]"],
}
```

---

## 觸發護欄時的情況

| 情況 | `hops` | `guardrail_hits` | 會呼叫 synthesize 嗎 | `answer` |
|---|---|---|---|---|
| 正常收斂（上例） | 3 個 | `[]` | ✅ | LLM 統整的完整答案 |
| 跑完 `recursion_limit` 輪仍沒收斂 | 所有輪次的 hops | `["recursion_limit"]` | ✅ | 用已有的資訊盡量回答 |
| `cost_units ≥ max_cost_units` | 已檢索到的部分 hops | `["cost_cap"]` | ✅ | 可能只回答一部分 |
| 超過 `global_timeout_seconds` | `[]` | `["global_timeout"]` | ❌ | `"多跳查詢在護欄限制內沒有取得足夠資訊，無法完整回答。"` |

`if hops:` 的判斷讓沒有任何檢索結果時直接回傳 fallback，省掉一次沒有意義的 LLM call。

