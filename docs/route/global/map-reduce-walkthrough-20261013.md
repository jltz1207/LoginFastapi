# GLOBAL 分支 `_map_reduce` 逐行導讀

> 查證日期：2026-10-13
> 依據：閱讀 [`global_summary.py`](../../../app/routing/branches/global_summary.py) 中 `GlobalSummaryBranch._map_reduce`（當時在第 87–110 行）的原始碼，**未實際執行**。延遲、呼叫次數是依程式邏輯推算，不是實測值。

相關筆記：
- [Query Router 架構導讀](../architecture/router-architecture-20261010.md)：GLOBAL 分支在整個 router 裡的位置
- [文件摘要存哪裡](document-summary-storage-20261007.md)：`_map_reduce` 吃的摘要從哪裡來
- [為什麼讀遍所有摘要，以及代價](full-scan-cost-20261013.md)：輸入沒有篩選、成本跟 KB 大小成正比

---

## 它要解決的問題

GLOBAL 問題（例如「這批文件整體在講什麼？」）要讀**整個知識庫每份文件的摘要**。50 份或 500 份摘要一次塞進 prompt 會超過 context window，也會變慢。

`_map_reduce` 的做法是**分批摘要，再把摘要的摘要繼續合併，直到剩一段**。這其實是一棵 reduce tree。

---

## 完整程式碼

```python
async def _map_reduce(self, query: str, texts: list[str]) -> str:
    if not texts:
        return ""
    sem = asyncio.Semaphore(self._guard_size)
    async def complete(prompt: str) -> str:
            llm = LLMFactory.get_model()
            response = await llm.ainvoke(prompt)
            content = response.content
            return content if isinstance(content, str) else str(content)
    async def _guarded(prompt: str) -> str:
        async with sem:
            return await complete(prompt)
    current = texts
    first_pass = True
    while len(current) > 1 or first_pass:
        batches = [current[i : i + self._batch_size] for i in range(0, len(current), self._batch_size)]
        results = list(
            await asyncio.gather(*(_guarded(_build_map_prompt(query, batch)) for batch in batches), return_exceptions=True)
        )
        current = [r for r in results if isinstance(r, str)]
        if not current:
            raise RuntimeError("all map batches failed")
        first_pass = False
    return current[0]
```

相關的建構參數（`GlobalSummaryBranch.__init__`）：

| 參數 | 預設 | 意義 |
|---|---|---|
| `batch_size` | 8 | 每次 LLM call 塞幾段文字；小於 2 會丟 `ValueError` |
| `guard_size` | 10 | 同時最多幾個 LLM call 在跑 |

---

## 逐行說明

### 函式簽名

```python
async def _map_reduce(self, query: str, texts: list[str]) -> str:
```

- `query`：使用者的問題（`state.resolved_query`）。
- `texts`：每份文件的 summary 字串，來自 `fetch_document_summaries()`。
- 回傳最後那一段文字，也就是給使用者的答案。

### 空輸入檢查

```python
if not texts:
    return ""
```

沒有輸入就直接回傳空字串。實際上呼叫端 `__call__` 已經先擋掉沒有摘要的情況（會回傳 `_NO_SUMMARIES_MESSAGE`），所以這是防禦性檢查。

### Semaphore：限制並行數

```python
sem = asyncio.Semaphore(self._guard_size)
```

建立一個 `Semaphore`，名額是 `guard_size`（預設 10），意思是**同一時間最多 10 個 LLM call 在跑**。500 份摘要第一輪有 63 個 batch，如果同時打 63 個 request，很容易撞到 Gemini 的 rate limit（429）。

- 注意它在每次呼叫 `_map_reduce` 時才建立，所以限制的是**單一 request 內**的並行數。10 個使用者同時問 GLOBAL 問題，最多還是會有 100 個 call 同時在跑。

### `complete`：呼叫一次 LLM

```python
async def complete(prompt: str) -> str:
        llm = LLMFactory.get_model()
        response = await llm.ainvoke(prompt)
        content = response.content
        return content if isinstance(content, str) else str(content)
```

- `LLMFactory.get_model()` 依 `settings` 建立 model。根據 CLAUDE.md，它每次呼叫都會 new 一個 instance。
- `ainvoke` 是 async 呼叫，等待 LLM 時 event loop 可以去跑其他 batch。
- LangChain 的 `response.content` 一般是 `str`，但 multimodal 或部分 provider 會回傳 `list`（content blocks）。這裡統一轉成 `str`。`str(list)` 會得到像 `"[{'type': 'text', ...}]"` 這種字串，只是保底，不是很好的轉換。
- 小地方：這個 function 的 body 縮排是 16 格，跟其他地方的 12 格不一致，屬於 cosmetic 問題，不影響執行。

### `_guarded`：套上 semaphore

```python
async def _guarded(prompt: str) -> str:
    async with sem:
        return await complete(prompt)
```

`async with sem` 會先拿一個名額，拿不到就等；`complete` 結束（不論成功或丟例外）就自動釋放。所以後面就算一次 `gather` 63 個 coroutine，真正同時在打 API 的也只有 10 個。

### 迴圈狀態

```python
current = texts
first_pass = True
```

- `current`：這一輪要處理的文字清單，一開始是原始摘要。
- `first_pass`：保證**至少跑一輪 LLM**，原因見下一段。

### 迴圈條件

```python
while len(current) > 1 or first_pass:
```

迴圈條件是「還不只一段」**或**「還沒跑過任何一輪」。

為什麼需要 `first_pass`？如果知識庫只有 **1 份**文件，`len(current) == 1`，沒有這個旗標就會直接 `return texts[0]`，把那份**原始摘要**原封不動當成答案回傳，完全沒有針對 `query` 回答。有了 `first_pass`，就算只有一份也會先經過 LLM，依問題萃取重點。

### 切 batch

```python
batches = [current[i : i + self._batch_size] for i in range(0, len(current), self._batch_size)]
```

把 `current` 每 `batch_size`（預設 8）個切成一組，最後一組可能不滿 8 個。

```
current = [s1 ... s50], batch_size = 8
range(0, 50, 8) → 0, 8, 16, 24, 32, 40, 48
batches = [s1-s8], [s9-s16], ..., [s49-s50]   → 7 組
```

### 核心：並行跑完一整輪

```python
results = list(
    await asyncio.gather(*(_guarded(_build_map_prompt(query, batch)) for batch in batches), return_exceptions=True)
)
```

從裡到外讀：

1. `_build_map_prompt(query, batch)`：把這組摘要串起來，再加上指示「針對問題『{query}』萃取相關重點，寫成一段精簡摘要」。
2. `_guarded(...)`：每組產生一個受 semaphore 限制的 coroutine（這時還沒開始執行）。
3. `asyncio.gather(*...)`：讓所有 batch **同時開跑**（實際並行數受 semaphore 限制），等全部結束，回傳的結果**順序跟 `batches` 一樣**。
4. `return_exceptions=True`：某個 batch 失敗時，**不會讓整個 `gather` 跟著失敗**，而是把那個 exception 物件放在結果清單的對應位置。如果不加這個參數，一個 batch 失敗，整輪就會丟例外。

### 過濾失敗結果

```python
current = [r for r in results if isinstance(r, str)]
```

只保留成功的結果（`str`），把 exception 物件丟掉，這些結果就是下一輪的輸入。

代價是**失敗的 batch 會被靜默丟棄**：那 8 份文件的資訊就從答案裡消失了，不會記 log，`trace` 也不會記錄。使用者會拿到看起來完整、但其實缺了一部分文件的答案。

### 全部失敗

```python
if not current:
    raise RuntimeError("all map batches failed")
```

整輪全部失敗時，沒有東西可以往下合併，只能丟錯。`GlobalSummaryBranch.__call__` 沒有 catch，所以這個例外會一路傳到 graph。

### 關掉 `first_pass`

```python
first_pass = False
```

第一輪結束後關掉旗標，之後只看 `len(current) > 1`。

### 回傳

```python
return current[0]
```

迴圈結束代表只剩一段，這段就是最終答案。

---

## 實際跑一次：500 份摘要

```mermaid
flowchart LR
    A["500 份摘要"] -- "第 1 輪：63 個 batch<br/>最多 10 個並行" --> B["63 段"]
    B -- "第 2 輪：8 個 batch" --> C["8 段"]
    C -- "第 3 輪：1 個 batch" --> D["1 段 = 答案"]
```

- LLM 呼叫總數：63 + 8 + 1 = **72 次**。
- 延遲：每一輪要等**最慢的那個 batch**，而且第 1 輪有 63 個 batch、只有 10 個名額，實際上要排 ⌈63/10⌉ = 7 波。所以程式碼 docstring 寫的「只等 3 個 round trip」只有在並行數不受限時才成立；第 1 輪其實比較接近 7 個 round trip（推論，未實測）。

## 為什麼一定會結束？

`__init__` 強制 `batch_size >= 2`。只要 `n > 1`，每一輪剩下的段數 ⌈n / batch_size⌉ 一定嚴格小於 `n`；失敗的 batch 被丟掉只會讓段數更少。所以段數每輪都會減少，最後一定會降到 1，或是全部失敗而丟錯。

---

## 觀察（推論，未驗證）

1. **每一輪都用同一個 prompt**：第 2 輪以後輸入的是「中間摘要」，但 prompt 還是寫「以下是幾份文件摘要…寫成一段精簡摘要」。最後一輪也一樣，所以最終輸出比較像「跟問題相關的重點摘要」，而不是直接回答問題。一般 map-reduce 會讓最後一輪用另一個「依這些重點回答問題」的 reduce prompt。
2. **失敗被吃掉、沒有記錄**：建議至少把失敗的 batch 數寫進 `trace`，例如 `global: docs=50 failed_batches=1`。
3. **沒有整體 timeout**：`multi_hop` 有 `global_timeout_seconds`，這裡沒有。如果某個 LLM call 卡住，整個 request 就會一起卡住。
4. **Semaphore 是 per-request**：並行上限不是全域的，同時有多個 GLOBAL request 時，總並行數會跟著倍增。
