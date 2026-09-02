import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import UUID, DateTime, Enum, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.session import Base


class KbStatusEnum(str, enum.Enum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"
    DELETED = "DELETED"


class AsistantKnowledgeBase(Base):
    __tablename__ = "AsistantKnowledgeBases"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True  
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True, nullable=False)
    title: Mapped[Optional[str]] = mapped_column(String, default="Untitled Knowledge Base")
    status: Mapped[KbStatusEnum] = mapped_column(
        Enum(
            KbStatusEnum,
            native_enum=False,
            create_constraint=True,
            length=50,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        default=KbStatusEnum.ACTIVE,
        server_default=KbStatusEnum.ACTIVE.value,
        nullable=False,
    )
    last_message_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

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
