import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, DateTime, Enum, Integer, Numeric, String, Text, ForeignKey,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.user import Base, TimestampMixin


class Booking(TimestampMixin, Base):
    __tablename__ = "bookings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    reference: Mapped[str] = mapped_column(
        String(10), unique=True, nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    trip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trips.id"), nullable=False
    )
    booked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    status: Mapped[str] = mapped_column(
        Enum(
            "pending", "confirmed", "checked_in", "completed",
            "cancelled", "expired", "no_show",
            name="booking_status",
        ),
        default="pending",
        server_default="pending",
    )
    total_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="NGN", server_default="NGN")
    passenger_count: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    contact_email: Mapped[str | None] = mapped_column(String(255))
    contact_phone: Mapped[str | None] = mapped_column(String(20))
    emergency_contact_name: Mapped[str | None] = mapped_column(String(200))
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(20))
    special_requests: Mapped[str | None] = mapped_column(Text)
    payment_method_hint: Mapped[str | None] = mapped_column(String(20))  # card, wallet, pay_at_terminal
    payment_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_reason: Mapped[str | None] = mapped_column(Text)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminder_24h_sent: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    reminder_1h_sent: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    user: Mapped["User"] = relationship(  # noqa: F821
        back_populates="bookings", foreign_keys=[user_id]
    )
    booked_by: Mapped["User | None"] = relationship(foreign_keys=[booked_by_user_id])  # noqa: F821
    trip: Mapped["Trip"] = relationship(back_populates="bookings")  # noqa: F821
    passengers: Mapped[list["BookingPassenger"]] = relationship(
        back_populates="booking", cascade="all, delete-orphan"
    )
    payments: Mapped[list["Payment"]] = relationship(back_populates="booking")  # noqa: F821
    review: Mapped["TripReview | None"] = relationship(back_populates="booking")  # noqa: F821


class BookingPassenger(Base):
    __tablename__ = "booking_passengers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    booking_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False
    )
    seat_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trip_seats.id"), nullable=False
    )
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    gender: Mapped[str | None] = mapped_column(
        Enum("male", "female", "other", name="gender_type", create_type=False)
    )
    phone: Mapped[str | None] = mapped_column(String(20))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    checked_in: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    qr_code_data: Mapped[str | None] = mapped_column(String(255), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    booking: Mapped["Booking"] = relationship(back_populates="passengers")
    seat: Mapped["TripSeat"] = relationship()  # noqa: F821
