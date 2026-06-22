# Python Enum matching the database ENUM state values
from datetime import datetime
import enum
from typing import Optional
import uuid

from sqlalchemy import UUID, BigInteger, DateTime, Enum, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class IngestionStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    INDEXED = "indexed"
    FAILED = "failed"

class Document(Base):
    __tablename__ = "documents"

    # --- Primary Key ---
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), 
        primary_key=True, 
        default=uuid.uuid4, 
        server_default=func.gen_random_uuid()
    )

    # --- Logical Relationships (No Physical DB Foreign Keys) ---
    # These are now plain UUID columns. PostgreSQL will not validate if these IDs exist.
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True  
    )

    # --- File Identity ---
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_extension: Mapped[str] = mapped_column(String(20), nullable=False) # pdf | docx | csv | etc.
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True) 

    # --- Storage Layer Details ---
    storage_bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1000), nullable=False)

    # --- Ingestion Pipeline States ---
    status: Mapped[IngestionStatus] = mapped_column(
        Enum(IngestionStatus),
        default=IngestionStatus.PENDING,
        nullable=False,
        index=True
    )
    chunk_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    token_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    indexed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- Extracted Content ---
    raw_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    page_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # --- Audit & Control Fields ---
    created_dt: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        server_default=func.now(),
        nullable=True
    )
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    modified_dt: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=True
    )
    modified_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)