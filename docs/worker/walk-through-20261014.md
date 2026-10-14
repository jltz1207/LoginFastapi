
## 逐行解讀

- **#1 → #2**：worker 送出 DB 查詢後停在 `await`。這 50 ms 內 loop 沒有其他事可做，就空轉等著。
- **#3**：chat 在 worker 等 download（在 thread pool 裡跑）時進來，loop 立刻切過去處理它。**worker 沒有做錯任何事，chat 也不用排隊等 worker。**
- **#4 和 #5 同一個 ms**：download 完成和 health 到達幾乎同時。Loop 先讓 worker 跑到下一個 `await`（把 parse 丟進 thread），再處理 health。Health 沒有任何 `await`，一次就跑完。
- **#6 → #10**：chat 的兩次等待，剛好落在 worker 的 parse 和 summary 期間。Chat 從進來到回應共 318 ms（93 → 411），和 100 + 200 = 300 ms 的理論值幾乎一樣。**Worker 的存在對 chat 幾乎沒有影響。**
- **#7 → #8**：upload 寫入 `doc-B` 後呼叫 `notify()`。這時 worker 正在處理 `doc-A`，沒有在 `wake.wait()` 上等，所以 `notify()` 只是把 Event 設成 set，**不會打斷 worker**。
- **#13 → #14**：`doc-A` 做完後，worker 的內層 `while db.pending` 直接看到 `doc-B`，接著處理。這裡**不是** `notify()` 叫醒的，是 claim 迴圈自己撈到的。
- **#21 → #23**：`doc-B` 做完後 worker 去等 `wake`，但 #8 設過的 Event 還沒被 clear，所以 `wait()` 立刻返回（#22），撈不到工作又回去等（#23）。這是一次**多餘但無害的喚醒**：claim 迴圈每次都重新檢查 DB，不會因為多醒一次而重複處理文件。

### 情境 2：parse PDF 直接在 loop 上跑

只改了一行：parse 從 `await asyncio.to_thread(time.sleep, 0.15)` 改成 `time.sleep(0.15)`。

```
 1      0 ms  [worker   ] claim doc-A -> await db (50)
 2     50 ms  [worker   ] doc-A: download -> await to_thread (100)
 3     97 ms  [chat     ] arrives -> await retrieval (100)
 4    151 ms  [worker   ] doc-A: parse PDF ON THE LOOP (150) - nobody else can run
 5    301 ms  [worker   ] doc-A: summary -> await llm.ainvoke (300)
 6    301 ms  [health   ] arrives -> respond 200 (no await)
 7    302 ms  [chat     ] -> await llm.ainvoke (200)
 8    302 ms  [upload   ] arrives -> await INSERT doc-B PENDING (30)
 9    335 ms  [upload   ] notify() worker, respond 202 (doc-B)
10    516 ms  [chat     ] respond 200
...（doc-B 的部分和情境 1 相同，省略）
```

```mermaid
sequenceDiagram
    participant Loop as event loop
    participant W as worker
    participant C as chat
    participant H as health
    participant U as upload

    Note over Loop: t=151
    Loop->>W: parse PDF（time.sleep，沒有 await）
    Note over Loop,U: t=151～301：loop 被 worker 佔住<br/>t=170 health 到了，進不來<br/>t=197 chat 的 retrieval 已經回來，繼續不了<br/>t=250 upload 到了，進不來
    W-->>Loop: t=301 終於 await llm（讓出）
    Loop->>H: 回 200（比情境 1 晚約 130 ms）
    Loop->>C: 呼叫 LLM（比情境 1 晚約 90 ms）
    Loop->>U: INSERT doc-B（比情境 1 晚約 40 ms）
```

#### 和情境 1 對照

| 事件 | 情境 1（ms） | 情境 2（ms） | 被拖延（情境 2 − 情境 1） |
|---|---|---|---|
| health 回應 | 167 | 301 | 約 130 ms |
| chat 開始呼叫 LLM | 214 | 302 | 約 90 ms |
| upload 開始 INSERT | 264 | 302 | 約 40 ms |
| chat 回應 | 411 | 516 | 約 105 ms |

重點：

- **#4 → #5 這 150 ms 裡，log 完全沒有其他 task 的紀錄**。三個 request 不是「變慢」，而是**完全被凍結**，直到 worker 自己碰到下一個 `await`。
- **#5～#8 都擠在 301～302 ms**：loop 一被釋放，所有積壓的 task 一次全部跑起來。
- **連沒有任何 I/O 的 health 都要等 130 ms。** 所以 event loop 被卡住時，連健康檢查都會逾時，load balancer 可能會因此判定這台機器掛了（推論）。
- **Worker 自己幾乎沒有變快**：`doc-A` 情境 1 在 881 ms 做完，情境 2 在 859 ms 做完，差距在誤差範圍內。把 parse 放在 loop 上，**對 worker 沒有好處，只會傷害 request**。

### 一句話總結

Event loop 像一個只有一位店員的櫃台：每個 task 輪到時都要自己說「我要等東西」（`await`），店員才能去服務下一位。Worker 只要每一步都會 `await`，就能和 request 共用這位店員；只要有一步不肯讓位（blocking 或 CPU-bound 直接跑在 loop 上），後面所有人都得排隊。

---