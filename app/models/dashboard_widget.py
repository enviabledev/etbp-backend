import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, String, ForeignKey
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class DashboardWidget(Base):
    __tablename__ = "dashboard_widgets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    widget_type: Mapped[str] = mapped_column(String(50), nullable=False)
    data_source: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    config: Mapped[dict | None] = mapped_column(JSON, default=dict)
    position: Mapped[dict | None] = mapped_column(JSON, default=lambda: {"x": 0, "y": 0, "w": 6, "h": 4})
    is_visible: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
