"""Count the tokens of every chat-model call made inside a block of code.

Used where the call site cannot read `usage_metadata` itself, e.g. structured output
(`with_structured_output()` returns the parsed object, not the `AIMessage`).

Not `langchain_core.callbacks.get_usage_metadata_callback()`, for two reasons:
- it registers a new configure hook on every call, and that global list never shrinks,
  so calling it per request leaks;
- it drops the usage of any response whose `response_metadata` has no `model_name`.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from langchain_core.tracers.context import register_configure_hook


class TokenCounter(BaseCallbackHandler):
    """Sums `usage_metadata["total_tokens"]` over every chat-model call it sees."""

    def __init__(self) -> None:
        self.total_tokens = 0
        self._lock = threading.Lock()

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        for generations in response.generations:
            for generation in generations:
                usage = getattr(getattr(generation, "message", None), "usage_metadata", None)
                if usage:
                    with self._lock:
                        self.total_tokens += usage.get("total_tokens", 0)


# Registered once at import; `count_llm_tokens()` only sets / resets the variable.
_token_counter_var: ContextVar[TokenCounter | None] = ContextVar("llm_token_counter", default=None)
register_configure_hook(_token_counter_var, inheritable=True)


@contextmanager
def count_llm_tokens() -> Iterator[TokenCounter]:
    """Every chat-model call in this block (including tasks it starts) reports to the yielded counter.

    Blocks nest: a call inside an inner block counts only toward the inner counter.
    """
    counter = TokenCounter()
    token = _token_counter_var.set(counter)
    try:
        yield counter
    finally:
        _token_counter_var.reset(token)
