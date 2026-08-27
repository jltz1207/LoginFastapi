# `ensemble_retriever | RunnableLambda(lambda docs: docs[:top_k])` 執行流程（`top_k=4` 範例）

以 `top_k=4` 為例，追一次 `app/rag/retriever/hybrid_retriever.py` 裡這行的資料怎麼流。

## 前置設定（`top_k=4` 往下傳）

```python
bm25_retriever.k = 4                                  # BM25 最多回 4 筆
dense_retriever = get_collection_retriever(..., top_k=4, ...)  # Chroma 最多回 4 筆
ensemble_retriever = EnsembleRetriever(
    retrievers=[bm25_retriever, dense_retriever],
    weights=(0.5, 0.5),
)
pipeline = ensemble_retriever | RunnableLambda(lambda docs: docs[:top_k])
```

## Step 1：`ensemble_retriever.ainvoke(query)` 內部做了什麼

假設對同一個 query，兩邊各自查回 4 筆（照 rank 1→4 排序）：

- BM25 排名：`[A, B, C, D]`
- Dense 排名：`[C, A, E, F]`

`EnsembleRetriever` 用的是 **weighted Reciprocal Rank Fusion**（實際裝的 `langchain_classic/retrievers/ensemble.py` 原始碼，常數 `c=60`）：

```
score(doc) = Σ_每個出現的清單  weight / (rank_in_that_list + c)
```

逐一算（`weight=0.5`）：

| doc | 在哪些清單出現 | 算式 | score |
|---|---|---|---|
| A | BM25 rank1, Dense rank2 | 0.5/61 + 0.5/62 | 0.01626 |
| C | BM25 rank3, Dense rank1 | 0.5/63 + 0.5/61 | 0.01613 |
| B | BM25 rank2 only | 0.5/62 | 0.00806 |
| E | Dense rank3 only | 0.5/63 | 0.00794 |
| D | BM25 rank4 only | 0.5/64 | 0.00781 |
| F | Dense rank4 only | 0.5/64 | 0.00781 |

`weighted_reciprocal_rank()` 把兩份清單 dedup（同一份 doc 在兩邊都出現時分數用**加總**的，這也是為什麼同時出現在兩邊的 A、C 分數最高——這正是 hybrid 檢索想要的效果：兩種方法都認同的文件排前面）、依分數由高到低排序，**但不截斷**，把全部 6 筆（4+4 各出現一次的 unique 文件）都回傳：

```
ensemble_retriever.ainvoke(query)
  → [A, C, B, E, D, F]      ← 6 筆，不是 4 筆
```

## Step 2：`RunnableLambda(lambda docs: docs[:top_k])`

LCEL 的 `|` 是把上一步的輸出整包當這一步的輸入，所以這個 `RunnableLambda` 收到的 `docs` 就是上面那 6 筆已經排序好的清單。`top_k` 是從外層 `get_retriever(self, user_id, knowledge_base_id, top_k=4)` 這個方法的參數 closure 進來的（每次呼叫 `get_retriever()` 都是獨立的區域變數，不會有 lambda 常見的迴圈晚繫結問題）：

```
docs[:4] → [A, C, B, E]     ← 只留 RRF 分數最高的前 4 筆，D、F 被丟掉
```

## 為什麼這樣接是對的、而且便宜

- `docs[:top_k]` 是純記憶體切片，不會多打一次 Chroma 或重算 BM25——`ensemble_retriever` 已經把該做的檢索都做完了，這一步只是「決定要留幾筆」。
- 因為 `weighted_reciprocal_rank()` 回傳前已經照 RRF 分數排序過，`[:top_k]` 拿到的保證是分數最高的前 `top_k` 筆，不是隨便截斷。
- 沒有這一步的話，呼叫端拿到的筆數會隨 query 浮動（兩邊重疊多就接近 `top_k`、重疊少就接近 `2*top_k`），跟 `BasicRetriever` 那種「保證回傳 ≤top_k 筆」的行為對不上——兩個 `RetrieverFactory` 實作對同一個參數的契約才會一致。
