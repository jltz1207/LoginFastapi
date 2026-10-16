# docs/ conventions

## File naming: date suffix

Every Markdown file under `docs/` must end with the date it was last written or updated, in `YYYYMMDD` format:

```
<name>-YYYYMMDD.md
```

Example: `vectorstore-retriever-structure-20261009.md`

- New file: use today's date.
- Updating an existing file: rename it to today's date with `git mv` (never plain `mv`), and do not auto-commit.
- A file without a date suffix that gets updated must be renamed to add one.
- After renaming, search `docs/`, `app/`, `CLAUDE.md`, and `README.md` for links to the old filename and update them.
- Exceptions: `README.md` and `progress.md` (index / tracking files) keep their fixed names.


## 撰寫docs

> 只在建立、修改、整理 Markdown 學習筆記時適用。寫程式、回答一般問題時不適用。

### 語言
- 內文用繁體中文，技術術語照「語言」一節保留英文。
- 例外：notes 裡 code block 的 comment 可以用繁中解釋概念；identifier、string literal 仍用英文。

### 結構
- 多用 mermaid 圖呈現結構、流程、時序；table 只在「多個項目 × 多個面向」的對照時才用，不要每一節都用。
- 一篇 docs 只講一個主題；主題變大就拆成資料夾，底下放多個檔案。

### 正確性
- code demo 要實際執行；note 裡的輸出和數字要來自實測，不能自己推測。
- 標上 `查證日期`，並附上實測時用的版本（Python、套件）。
- 沒有驗證過的說法要明確標示為推論或未查證。
- 實測結果和常見說法不同時，照實寫，並註明差異。

### 維護
- 不要主動修改 `docs/` 裡的文件：改程式碼時，即使既有 docs 因此過時，也不要自己標註「已修正」、改寫段落或改名。使用者會定期 review docs。
  - 改完程式碼後，在回覆中列出可能過時的 docs 檔案與段落即可。
  - 只有使用者明確要求寫入或修改 docs 時才動手。
- 還沒寫的主題只列在 README，不要建立空檔。
- 跨檔引用用相對路徑的 markdown link；搬移或改名後，檢查所有相對 link 是否還連得到。
- 搬移檔案用 `git mv`；不要自動 commit。
- 需要寫多篇的學習主題，用該資料夾的 `progress.md` 記錄架構決策、完成狀態與待辦。
