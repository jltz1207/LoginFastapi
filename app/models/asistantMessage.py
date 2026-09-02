import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, UUID, DateTime, Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.session import Base


class RoleEnum(str, enum.Enum):
    USER = "USER"
    AI = "AI"
    SYSTEM = "SYSTEM"


class MsgStatusEnum(str, enum.Enum):
    SENT = "SENT"
    STREAMING = "STREAMING"
    FAILED = "FAILED"


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
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True  
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)

    role: Mapped[RoleEnum] = mapped_column(
            Enum(
                RoleEnum,
                native_enum=False,
                create_constraint=True,
                length=50,
                values_callable=lambda enum_cls: [e.value for e in enum_cls],
            ),
            default=RoleEnum.USER,
            server_default=RoleEnum.USER.value,
            nullable=False,
    )
    content: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[MsgStatusEnum] = mapped_column(
        Enum(
            MsgStatusEnum,
            native_enum=False,
            create_constraint=True,
            length=50,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        default=MsgStatusEnum.SENT,
        server_default=MsgStatusEnum.SENT.value,
        nullable=False,
    )
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
