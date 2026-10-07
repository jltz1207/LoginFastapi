# SQLAlchemy Enum 欄位：五種做法對比

## 0. 前提：型別解析優先序

`mapped_column()` 一旦收到 explicit type argument，那個型別就贏，`Mapped[...]` annotation **完全不參與**型別解析。

```python
status: Mapped[KbStatusEnum] = mapped_column(String(50))
#              ^^^^^^^^^^^^^ 被靜默忽略
```

例外：annotation 的 `Optional` 與否**仍然**會被用來推導 `nullable`。所以 annotation 是「部分被採用」，這讓人更容易誤以為整個 annotation 都生效了。

SQLAlchemy 不會檢查兩者是否一致，也不會警告。

---

## 1. 五種做法總覽

| | DB 型別 | 讀回型別 | 寫入驗證 | 新增 member | annotation 誠實 |
|---|---|---|---|---|---|
| **A** `Enum(E)` native | PG enum type | `E` | ✅ bind 期 | ❌ `ALTER TYPE` | ✅ |
| **B** `Enum(E, native_enum=False)` | VARCHAR + CHECK | `E` | ✅ bind 期 | ✅ 改 CHECK | ✅ |
| **C** `String` + `Mapped[str]` | VARCHAR | `str` | ❌ | ✅ 無約束 | ✅ |
| **D** `String` + `Mapped[E]` | VARCHAR | `str` | ❌ | ✅ 無約束 | ❌ **說謊** |
| **E** `TypeDecorator` | 自訂 | `E` | ✅ 自己寫 | ✅ | ✅ |

**結論先講**：預設選 **B**。D 是 anti-pattern，必須修掉。

---

## 2. A — `Enum(KbStatusEnum)`（native enum）

```python
status: Mapped[KbStatusEnum] = mapped_column(Enum(KbStatusEnum))
```

Postgres 上會 `CREATE TYPE kbstatusenum AS ENUM (...)`。

**優點**
- DB 層真正的型別約束，任何 client（含 raw SQL、其他服務）都擋得住髒值。
- 儲存緊湊（4 bytes OID），比 VARCHAR 省空間。
- `\dT` 直接看得到定義，schema 自我描述。

**缺點 — 這是多數團隊逃離 A 的原因**
- 新增 member 要 `ALTER TYPE ... ADD VALUE`，PG 12 以前**不能在 transaction 內執行**，Alembic migration 會很難寫。
- **無法刪除 member**，也不能改順序。要改就得建新 type → 改欄位 → drop 舊 type，三步。
- 跨 DB 不可攜（SQLite / MySQL 行為不同）。

**適用**：enum 值極穩定、幾乎不會變動（例如 `currency`、`weekday`）。

---

## 2.5 A vs B 白話版：native enum 跟 `native_enum=False` 到底差在哪

> 一句話：兩者在 **Python 程式裡完全一樣**，差別只在 **DB 怎麼儲存和檢查這個欄位**。

### 生活比喻

限制「衣服尺寸只能是 S/M/L」：

- **native enum** = 去政府登記一個官方分類「尺寸」，法定只有 S/M/L，全國表格都能引用。但要加 XL 得走修法程序，而且**已登記的分類永遠不能刪**。
- **VARCHAR + CHECK** = 表格上就是個普通文字欄，旁邊寫一條規則「只准填 S/M/L」。要加 XL？把規則擦掉重寫一條就好，要刪也一樣。

使用者填表體驗一模一樣（都只能填 S/M/L）。差別在**你以後想改規則有多麻煩**。

### DB 裡實際長什麼樣

```sql
-- native enum：先造一個「型別」，欄位再用這個型別
CREATE TYPE size AS ENUM ('S', 'M', 'L');
CREATE TABLE shirts (size  size);

-- VARCHAR + CHECK：欄位是普通文字，加一條檢查規則
CREATE TABLE shirts (size  VARCHAR(50) CHECK (size IN ('S','M','L')));
```

一個是**獨立的 type object**，一個是**欄位上的 check constraint**。

### 為什麼這個差別重要

因為 `status` 這種欄位**幾乎一定會加值**。今天有 ACTIVE / ARCHIVED / PENDING，明天產品說要加 SUSPENDED。

**native enum 加值 —— 麻煩**
```sql
ALTER TYPE kbstatusenum ADD VALUE 'SUSPENDED';
```
舊版 PG 不能包在一般 migration 裡跑，要特別處理，而且**加錯了刪不掉**（PG 根本沒有「刪除 enum 值」這功能）。

**VARCHAR + CHECK 加值 —— 簡單**
```sql
-- 把舊規則換成新規則就好
CHECK (status IN ('ACTIVE','ARCHIVED','PENDING','SUSPENDED'))
```
普通 migration 搞定，加錯也能改回來。

### Python 端完全沒差

```python
row.status                          # 兩種都拿到 KbStatusEnum.ACTIVE（真 enum）
row.status.value                    # 兩種都能用
row.status = KbStatusEnum.PENDING   # 兩種都能寫
```

SQLAlchemy 幫你處理好轉換，Python 裡感覺不到不同。

### 一句話結論

| 你在意的事 | 選哪個 |
|---|---|
| 這欄位以後會加值（例如 `status`） | **VARCHAR + CHECK**（`native_enum=False`） |
| 值永遠不變（例如星期一到日） | native enum 也可以 |

`status` 註定會變 → **現在多打幾個字設定，換以後每次改值都輕鬆**。

（下面第 3 節有完整的 DDL 對照、排序行為、Alembic 陷阱、遷移 migration 等深入細節。）

---

## 3. B — `Enum(E, native_enum=False)` ✅ 推薦預設

```python
status: Mapped[KbStatusEnum] = mapped_column(
    Enum(
        KbStatusEnum,
        native_enum=False,
        length=50,
        values_callable=lambda e: [m.value for m in e],
    )
)
```

DDL 產出 `VARCHAR(50)` + `CHECK (status IN (...))`。

**為什麼是最佳折衷**
- SQLAlchemy 層負責雙向轉換 → 讀回來是真 enum。
- bind 期驗證仍在 → 寫入非法值直接 `LookupError`。
- DB 層只是 CHECK constraint → 新增 member 只需 `DROP CONSTRAINT` + `ADD CONSTRAINT`，可在 transaction 內跑。
- 跨 DB 可攜。

**`values_callable` 一定要加**

`Enum()` 預設存的是 member **name** 而非 **value**：

```python
class KbStatusEnum(str, Enum):
    PENDING = "pending"

# 預設行為 → DB 存 "PENDING"
# 加 values_callable → DB 存 "pending"
```

若目前 `value == name` 看不出差別，但哪天有人寫出兩者不同的 member，預設行為會與既有資料不一致。**現在就釘死。**

**注意**：`native_enum=False` 產生的 CHECK constraint 需要有名字才好在 migration 中操作，記得配 naming convention。

### 深入：A 跟 B 的完整差異表

| | native enum (A) | VARCHAR + CHECK (B) |
|---|---|---|
| DB 物件 | 獨立 type，可跨表共用 | 綁在單一欄位 |
| 儲存 | 4 bytes OID | 實際字串長度 |
| 新增 member | `ALTER TYPE ... ADD VALUE` | drop + recreate constraint |
| 刪除 member | **做不到** | 直接改 constraint |
| 改順序 | 做不到 | 無所謂（本來就沒順序） |
| 排序行為 | 依**宣告順序** | 依字典序 |
| 跨 DB | 只有 PG 支援 | 到處都能跑 |

### 陷阱一：排序行為不同

native enum 的比較與排序依**宣告順序**：

```sql
-- native：PENDING < ACTIVE < ARCHIVED（依 CREATE TYPE 的宣告順序）
-- VARCHAR：ACTIVE < ARCHIVED < PENDING（字典序）
SELECT * FROM knowledge_bases ORDER BY status;
```

若有 `ORDER BY status` 依賴語意順序的邏輯，從 A 換到 B 會**靜默改變結果**。實務上這種依賴不多，但要檢查。

### 陷阱二：Alembic autogenerate 偵測不到 native enum 的 member 變化

native enum 惡名昭彰的坑：`alembic revision --autogenerate` **不會偵測 enum member 的增減**。你在 Python 加了 member，autogenerate 產出**空 migration**，deploy 後寫入直接 `InvalidTextRepresentation`。

B 在有設 naming convention 的前提下，constraint 變更偵測得到。

### 刪除 member 是 native enum 的死穴

PG 沒有 `ALTER TYPE ... DROP VALUE`，永遠不會有。要移除一個值，完整流程：

```sql
CREATE TYPE kbstatusenum_new AS ENUM (...);
ALTER TABLE knowledge_bases
  ALTER COLUMN status TYPE kbstatusenum_new
  USING status::text::kbstatusenum_new;
DROP TYPE kbstatusenum;
ALTER TYPE kbstatusenum_new RENAME TO kbstatusenum;
```

`ALTER COLUMN TYPE` 會**全表重寫並持 ACCESS EXCLUSIVE lock**，大表等於停機。多表引用時每張表都要走一次。

### 從 native (A) 遷到 B 的 migration

```python
def upgrade():
    op.alter_column("knowledge_bases", "status",
                    type_=sa.String(50),
                    existing_type=sa.Enum(name="kbstatusenum"),
                    postgresql_using="status::text")
    op.create_check_constraint("ck_kb_status", "knowledge_bases",
                               "status IN ('ACTIVE','ARCHIVED','PENDING')")
    op.execute("DROP TYPE kbstatusenum")
```

這步同樣全表重寫加鎖，挑低流量時段做。**一次性痛換長期輕鬆**——之後每次 member 變更都是輕量 constraint 操作。

---

## 4. C — `String` + `Mapped[str]`

```python
status: Mapped[str] = mapped_column(String(50))
```

**誠實但陽春**。annotation 與 runtime 一致，不會騙人。

**代價**
- 讀取端每個 call site 自己 `KbStatusEnum(row.status)`，遲早漏。
- 完全沒有寫入驗證，`"activ"` typo 靜默入庫。
- type checker 無法做 exhaustiveness check。

**適用**：純資料搬運層、值域由外部系統定義而你無權約束時。一般業務 model 不建議。

---

## 5. D — `String` + `Mapped[E]` ❌ anti-pattern

```python
status: Mapped[KbStatusEnum] = mapped_column(String(50))  # 現況
```

### 5.1 實測 round-trip

```
stored in DB : 'ACTIVE'
read back as : 'ACTIVE'  (type str)   ← 不是 KbStatusEnum
```

- `isinstance(row.status, KbStatusEnum)` → `False`
- `row.status.value` → `AttributeError`
- `row.status is KbStatusEnum.ACTIVE` → `False`
- `row.status == KbStatusEnum.ACTIVE` → `True`（僅靠 `str` mixin + `value == name` 的巧合）

### 5.2 三個核心危害

**(1) type checker 主動為壞掉的程式碼背書**

`row.status.value` 在 mypy/pyright 下完全通過，runtime 才炸。static analysis 不只是沒幫上忙，而是給了**錯誤的安全感**。

連帶 exhaustiveness check 失效：

```python
match row.status:
    case KbStatusEnum.ACTIVE: ...
    case KbStatusEnum.ARCHIVED: ...
    # type checker 認為窮盡了
    # 走 class pattern（case KbStatusEnum():）則一個都不會 match
```

**(2) 遺失寫入驗證 → data integrity 退化**

這是會累積成資料災難的那個，比讀取端型別問題嚴重：

```python
row.status = "activ"     # typo，靜默入庫
row.status = "ACTIVE "   # 尾巴空白，靜默入庫
```

加上 migration、raw SQL、其他服務直連 DB 的路徑，欄位遲早有髒值。

**(3) `==` 不只失效，還會 false positive**

- False negative：有人寫 `PENDING = "pending"`（value ≠ name），比較靜默失效。
- False positive：
  ```python
  # row.status 實際是 str "ACTIVE"
  row.status == DocStatusEnum.ACTIVE   # True —— 完全不同的 enum
  ```
  真 enum 跨類別比較必定 `False`。現在只要 value 撞字串就相等，**跨 domain 的型別隔離整個消失**。

---

## 6. E — `TypeDecorator`

```python
class EnumAsString(TypeDecorator):
    impl = String
    cache_ok = True

    def __init__(self, enum_cls, *args, **kw):
        self._enum_cls = enum_cls
        super().__init__(*args, **kw)

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return self._enum_cls(value).value

    def process_result_value(self, value, dialect):
        return None if value is None else self._enum_cls(value)
```

**何時才需要**
- 值域由外部系統定義，DB 中必然存在 enum 未涵蓋的值 → 需要 fallback 而非拋錯。
- 需要 legacy 值 alias（DB 存 `"A"`，程式用 `ACTIVE`）。
- 需要客製化 NULL / 空字串處理。

否則用 B 就好，別自己重造。

**注意**：`cache_ok = True` 一定要設，否則 SQLAlchemy 的 compiled query cache 會失效，影響效能。

---

## 7. 從 D 遷移出去的落地順序

1. **先把 annotation 改成 `Mapped[str]`**
   一行的事，立刻停止說謊，讓 type checker 開始報出所有 `.value` 的呼叫點。
   零風險，且能**立刻量化影響範圍**——報得少代表後面幾步快，報得多代表問題已滲透得比想像深。

2. **掃 DB 現有資料，清髒值**
   改成 `Enum` 後髒值會在**讀取時**炸 `LookupError`。必須先清乾淨。
   ```sql
   SELECT status, count(*) FROM knowledge_bases GROUP BY status;
   ```

3. **換成 `Enum(..., native_enum=False, values_callable=...)`**
   補上 CHECK constraint 的 migration。

4. **annotation 改回 `Mapped[KbStatusEnum]`**

---

## 8. 附帶陷阱：`class X(str, Enum)` 的 `__format__`

Python 3.11 前後行為有改：

```python
f"{KbStatusEnum.ACTIVE}"   # 3.10 → ? / 3.11+ → ?
```

輸出是 `"ACTIVE"` 還是 `"KbStatusEnum.ACTIVE"` 跨版本不一致。若 log 或 error message 有字串插值，升版時會**靜默改變輸出格式**。

→ 在你們的目標 Python 版本上實測確認，別靠記憶。
→ 3.11+ 可考慮改用 `StrEnum`，行為明確。

---

## 9. 一句話速查

- 值域穩定、要 DB 層強約束 → **A**
- 一般業務 model → **B**（預設選這個）
- 純搬運、值域不由你定 → **C**
- 需要 fallback / alias / 客製轉換 → **E**
- **D 永遠不要**