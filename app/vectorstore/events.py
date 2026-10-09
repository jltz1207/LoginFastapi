"""Change notifications for knowledge bases stored in the vector store.

The vector store layer cannot import the caches built on top of it (e.g.
`app/rag/retriever/bm25_index.py` already imports `StoreIndexer`), so those caches
register here instead, and `StoreIndexer` notifies after every write. Any code that
changes a knowledge base's chunks must go through `StoreIndexer` for this to work.
"""

import threading
import weakref
from collections.abc import Callable

from app.core.logging import get_logger

logger = get_logger(__name__)

KbChangedListener = Callable[[str, str, str], None]

# Bound methods are held weakly so a discarded cache (e.g. after lru_cache.cache_clear()
# in tests) is not kept alive, and stops receiving notifications.
_listeners: list[weakref.WeakMethod] = []
_lock = threading.Lock()


def on_kb_changed(listener: KbChangedListener) -> None:
    """Register a bound method called as `listener(tenant_id, user_id, knowledge_base_id)`."""
    with _lock:
        _listeners.append(weakref.WeakMethod(listener))


def notify_kb_changed(tenant_id: str, user_id: str, knowledge_base_id: str) -> None:
    with _lock:
        _listeners[:] = [ref for ref in _listeners if ref() is not None]
        listeners = [ref() for ref in _listeners]
    for listener in listeners:
        if listener is None:
            continue
        try:
            listener(tenant_id, user_id, knowledge_base_id)
        except Exception:
            # A failing cache must not fail the write that triggered it.
            logger.exception(
                "vectorstore.kb_changed.listener_failed",
                tenant_id=tenant_id,
                user_id=user_id,
                knowledge_base_id=knowledge_base_id,
            )
