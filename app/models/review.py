import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text, ForeignKey, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.user import Base


class TripReview(Base):
    __tablename__ = "trip_reviews"
    __table_args__ = (
        CheckConstraint("overall_rating >= 1 AND overall_rating <= 5", name="ck_overall_rating"),
        CheckConstraint(
            "driver_rating IS NULL OR (driver_rating >= 1 AND driver_rating <= 5)",
            name="ck_driver_rating",
        ),
        CheckConstraint(
            "bus_condition_rating IS NULL OR (bus_condition_rating >= 1 AND bus_condition_rating <= 5)",
            name="ck_bus_condition_rating",
        ),
        CheckConstraint(
            "punctuality_rating IS NULL OR (punctuality_rating >= 1 AND punctuality_rating <= 5)",
            name="ck_punctuality_rating",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    booking_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bookings.id"), unique=True, nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    trip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trips.id"), nullable=False
    )
    overall_rating: Mapped[int] = mapped_column(Integer, nullable=False)
    driver_rating: Mapped[int | None] = mapped_column(Integer)
    bus_condition_rating: Mapped[int | None] = mapped_column(Integer)
    punctuality_rating: Mapped[int | None] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(Text)
    is_visible: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    booking: Mapped["Booking"] = relationship(back_populates="review")  # noqa: F821
    user: Mapped["User"] = relationship()  # noqa: F821
    trip: Mapped["Trip"] = relationship()  # noqa: F821
