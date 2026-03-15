import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.user import Base, TimestampMixin


class Driver(TimestampMixin, Base):
    __tablename__ = "drivers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        unique=True, nullable=False,
    )
    license_number: Mapped[str] = mapped_column(String(50), nullable=False)
    license_expiry: Mapped[date] = mapped_column(nullable=False)
    license_class: Mapped[str | None] = mapped_column(String(10))
    years_experience: Mapped[int | None] = mapped_column(Integer)
    medical_check_expiry: Mapped[date | None] = mapped_column()
    rating_avg: Mapped[float] = mapped_column(Float, default=0.0, server_default="0.0")
    total_trips: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    assigned_terminal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("terminals.id")
    )

    user: Mapped["User"] = relationship()  # noqa: F821
    assigned_terminal: Mapped["Terminal | None"] = relationship()  # noqa: F821
    trips: Mapped[list["Trip"]] = relationship(back_populates="driver")  # noqa: F821
