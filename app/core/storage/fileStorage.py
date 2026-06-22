from pathlib import Path
import uuid
from app.core.logging import get_logger
from app.core.storage.client import get_storage_root

logger = get_logger(__name__)
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".csv", ".html", ".md"}
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB
 
 
# ── key / path helpers ────────────────────────────────────────────────────────
 
def _build_storage_key(user_id: str, knowledge_base_id: str, filename: str) -> str:
    """
    Relative path used as the storage key.
    e.g. users/usr_123/kb_456/f47ac10b_contract.pdf
 
    Namespaced so files are logically isolated per user and KB, mirroring
    the same isolation contract as the vectorstore (user_id → collection,
    knowledge_base_id → metadata filter).
    """
    unique = uuid.uuid4().hex[:8]
    safe_name = Path(filename).name  # strip any path traversal attempts
    return f"users/{user_id}/{knowledge_base_id}/{unique}_{safe_name}"
 
 
def _resolve_path(storage_key: str) -> Path:
    """Turn a storage key into an absolute local path."""
    return get_storage_root() / storage_key

def upload_file(
    user_id: str,
    knowledge_base_id: str,
    file_bytes: bytes,
    filename: str,
    mime_type: str,  # accepted for interface parity with S3 version; unused locally
) -> dict[str, str]:
    """
    Write file_bytes to disk.
 
    Returns a dict with storage_bucket and storage_key — store both on the
    document row in Postgres. storage_bucket is the storage root path so the
    file can always be located even if STORAGE_ROOT changes between deploys.
    """
    key = _build_storage_key(user_id, knowledge_base_id, filename)
    abs_path = _resolve_path(key)
 
    # ensure the per-KB directory exists
    abs_path.parent.mkdir(parents=True, exist_ok=True)
 
    abs_path.write_bytes(file_bytes)
 
    logger.info(
        "file_uploaded_local",
        user_id=user_id,
        knowledge_base_id=knowledge_base_id,
        path=str(abs_path),
        size_bytes=len(file_bytes),
    )
 
    return {
        "storage_bucket": str(get_storage_root()),  # root dir as "bucket"
        "storage_key": key,
    }
 
def download_file(storage_bucket: str, storage_key: str) -> bytes:
    """
    Read raw bytes from disk.
 
    Called by the ingestion pipeline to extract text from the original file.
    storage_bucket is ignored locally (we always resolve from STORAGE_ROOT)
    but kept in the signature for S3 interface parity.
    """
    abs_path = _resolve_path(storage_key)
 
    if not abs_path.exists():
        raise FileNotFoundError(
            f"File not found on local disk: {abs_path}. "
            "If you're running multiple workers or servers, switch to S3."
        )
 
    return abs_path.read_bytes()

def download_file(storage_bucket: str, storage_key: str) -> bytes:
    """
    Read raw bytes from disk.
 
    Called by the ingestion pipeline to extract text from the original file.
    storage_bucket is ignored locally (we always resolve from STORAGE_ROOT)
    but kept in the signature for S3 interface parity.
    """
    abs_path = _resolve_path(storage_key)
 
    if not abs_path.exists():
        raise FileNotFoundError(
            f"File not found on local disk: {abs_path}. "
            "If you're running multiple workers or servers, switch to S3."
        )
 
    return abs_path.read_bytes()