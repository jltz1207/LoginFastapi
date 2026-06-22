"""
Local disk storage client — development only.

Mirrors the same interface as the S3 client so the rest of the app
(document_service, ingestion worker) never needs to know which backend
is active. Swap to S3 in production by changing STORAGE_BACKEND in
your .env without touching anything else.

Directory layout on disk:
    STORAGE_ROOT/
    └── users/
        └── {user_id}/
            └── {knowledge_base_id}/
                └── {unique_prefix}_{filename}
"""

from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def get_storage_root() -> Path:
    """Return the root storage directory, creating it if it doesn't exist."""
    root = Path(settings.LOCAL_STORAGE_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    return root