import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, String, Text, ForeignKey
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class LostFoundReport(Base):
    __tablename__ = "lost_found_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    reporter_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    report_type: Mapped[str] = mapped_column(String(10), nullable=False)
    booking_ref: Mapped[str | None] = mapped_column(String(50))
    trip_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("trips.id"))
    route_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("routes.id"))
    terminal_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("terminals.id"), index=True)
    item_description: Mapped[str] = mapped_column(Text, nullable=False)
    item_category: Mapped[str] = mapped_column(String(50), nullable=False)
    color: Mapped[str | None] = mapped_column(String(50))
    distinguishing_features: Mapped[str | None] = mapped_column(Text)
    date_lost_found: Mapped[date] = mapped_column(Date, nullable=False)
    location_details: Mapped[str | None] = mapped_column(Text)
    contact_phone: Mapped[str] = mapped_column(String(50), nullable=False)
    contact_email: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="reported", server_default="reported", index=True)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    resolution_notes: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    images: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
