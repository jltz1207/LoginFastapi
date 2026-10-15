"""META 子意圖：router（`router/rule.py`）與 META 分支（`branches/meta.py`）共用的唯一 pattern 來源。

兩邊原本各寫一份（rule 用 regex、branch 用 substring），導致 router 判成 META 的問題
在 branch 落到 `default` 回覆。現在 rule 的 META 判斷就是 `classify_meta_intent(q) is not None`，
branch 再用同一個函式選模板，兩邊不可能再分歧。

這裡的 pattern 會直接以 0.95 信心短路 cascade，所以必須錨定在「你」/「系統」這類指向系統本身的字，
不要加 `什麼功能` 這種裸片語——「這份文件提到什麼功能」會被誤判成 META。
修改任何 pattern 都要遞增 `router/rule.py` 的 `ROUTER_VERSION`。
"""

from __future__ import annotations

import re
from enum import Enum


class MetaIntent(str, Enum):
    IDENTITY = "identity"
    CAPABILITIES = "capabilities"


# 順序即優先序：同時命中時（"你是誰？你能做什麼？"）取第一個。
META_INTENT_PATTERNS: dict[MetaIntent, list[re.Pattern[str]]] = {
    MetaIntent.IDENTITY: [
        re.compile(r"你是(誰|什麼)"),
        re.compile(r"你是?用(什麼|哪一?個|哪種)模型"),
        re.compile(r"\bwho are you\b", re.IGNORECASE),
        re.compile(r"\bwhat model are you\b", re.IGNORECASE),
    ],
    MetaIntent.CAPABILITIES: [
        re.compile(r"你(能|可以).{0,6}(做|提供|回答|協助)"),
        re.compile(r"系統.{0,6}(有|能|支援).{0,6}功能"),
        re.compile(r"我可以問你什麼"),
        re.compile(r"\bwhat can you (do|help)\b", re.IGNORECASE),
    ],
}


def classify_meta_intent(query: str) -> MetaIntent | None:
    for intent, patterns in META_INTENT_PATTERNS.items():
        if any(p.search(query) for p in patterns):
            return intent
    return None
