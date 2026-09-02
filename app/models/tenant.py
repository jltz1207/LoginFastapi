from datetime import datetime
import enum
from typing import Optional

from sqlalchemy import DateTime, Integer, String, UUID, Enum
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.session import Base

import uuid

class TenantStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    PENDING = "PENDING"
    SUSPENDED = "SUSPENDED"
    ARCHIVED = "ARCHIVED"

class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
            UUID(as_uuid=True),
            primary_key=True,
            default=uuid.uuid4,
            server_default=func.gen_random_uuid(),
        )

    name: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)

    '''
    slug:
    URL 與設定檔中使用的識別字串，例如 taiwan-mobile。
    這是 name 與 id 之間的折衷：比 UUID 好記好念，又比 name 穩定且安全。典型用途是 subdomain（taiwan-mobile.yourapp.com）或 API path。
    '''
    slug: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)

    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    status: Mapped[TenantStatus] = mapped_column(
        Enum(
            TenantStatus,
            native_enum=False,
            create_constraint=True,
            length=50,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        default=TenantStatus.PENDING,
        server_default=TenantStatus.PENDING.value,
        nullable=False,
    )
    # audit columns
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
