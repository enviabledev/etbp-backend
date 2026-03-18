import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class Banner(Base):
    __tablename__ = "banners"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    heading: Mapped[str | None] = mapped_column(String(200))
    body_text: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    background_color: Mapped[str | None] = mapped_column(String(10))
    text_color: Mapped[str | None] = mapped_column(String(10))
    cta_text: Mapped[str | None] = mapped_column(String(100))
    cta_action: Mapped[str | None] = mapped_column(String(20))
    cta_value: Mapped[str | None] = mapped_column(Text)
    placement: Mapped[str] = mapped_column(String(50), nullable=False, default="home_hero", index=True)
    target_audience: Mapped[str] = mapped_column(String(50), default="all")
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    impressions: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    clicks: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
