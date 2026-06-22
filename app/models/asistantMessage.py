import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, UUID, DateTime, Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.session import Base


class RoleEnum(enum.Enum):
    USER = 1
    ASISTANT = 2
    SYSTEM = 3


class StatusEnum(enum.Enum):
    SENT = 1
    STREAMING = 2
    FAILED = 3


class AsistantMessage(Base):
    __tablename__ = "AsistantMessages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True, nullable=False)
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True, nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[RoleEnum] = mapped_column(Enum(RoleEnum), nullable=False)
    content: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[StatusEnum] = mapped_column(Enum(StatusEnum), default=StatusEnum.SENT)
    model: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    prompt_tokens: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    completion_tokens: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    sources: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    created_dt: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        server_default=func.now(),
        nullable=True,
    )
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    modified_dt: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=True,
    )
    modified_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
