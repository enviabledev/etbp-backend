import uuid
from datetime import date, datetime, time, timezone

from sqlalchemy import (
    Boolean, DateTime, Enum, Float, Integer, JSON, Numeric, String, Text, Time,
    ForeignKey, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.user import Base, TimestampMixin


class Schedule(TimestampMixin, Base):
    __tablename__ = "schedules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    route_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("routes.id"), nullable=False
    )
    vehicle_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vehicle_types.id"), nullable=False
    )
    departure_time: Mapped[time] = mapped_column(Time, nullable=False)
    recurrence: Mapped[str | None] = mapped_column(String(100))  # e.g. "daily", "mon,wed,fri"
    valid_from: Mapped[date | None] = mapped_column()
    valid_until: Mapped[date | None] = mapped_column()
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    price_override: Mapped[float | None] = mapped_column(Numeric(12, 2))

    route: Mapped["Route"] = relationship(back_populates="schedules")  # noqa: F821
    vehicle_type: Mapped["VehicleType"] = relationship(back_populates="schedules")  # noqa: F821
    trips: Mapped[list["Trip"]] = relationship(back_populates="schedule")


class Trip(TimestampMixin, Base):
    __tablename__ = "trips"
    __table_args__ = (
        UniqueConstraint("schedule_id", "departure_date", name="uq_schedule_departure_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schedules.id")
    )
    route_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("routes.id"), nullable=False
    )
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vehicles.id")
    )
    driver_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id")
    )
    departure_date: Mapped[date] = mapped_column(nullable=False, index=True)
    departure_time: Mapped[time] = mapped_column(Time, nullable=False)
    actual_departure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_arrival_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(
        Enum(
            "scheduled", "boarding", "departed", "en_route", "arrived", "completed", "cancelled", "delayed",
            name="trip_status",
        ),
        default="scheduled",
        server_default="scheduled",
    )
    price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    available_seats: Mapped[int] = mapped_column(Integer, nullable=False)
    total_seats: Mapped[int] = mapped_column(Integer, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    inspection_data: Mapped[dict | None] = mapped_column(JSON)
    summary_data: Mapped[dict | None] = mapped_column(JSON)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    schedule: Mapped["Schedule | None"] = relationship(back_populates="trips")
    route: Mapped["Route"] = relationship(back_populates="trips")  # noqa: F821
    vehicle: Mapped["Vehicle | None"] = relationship(back_populates="trips")  # noqa: F821
    driver: Mapped["Driver | None"] = relationship(back_populates="trips")  # noqa: F821
    seats: Mapped[list["TripSeat"]] = relationship(
        back_populates="trip", cascade="all, delete-orphan"
    )
    bookings: Mapped[list["Booking"]] = relationship(back_populates="trip")  # noqa: F821
    incidents: Mapped[list["TripIncident"]] = relationship(back_populates="trip", cascade="all, delete-orphan")


class TripSeat(Base):
    __tablename__ = "trip_seats"
    __table_args__ = (
        UniqueConstraint("trip_id", "seat_number", name="uq_trip_seat_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    trip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False
    )
    seat_number: Mapped[str] = mapped_column(String(10), nullable=False)
    seat_row: Mapped[int | None] = mapped_column(Integer)
    seat_column: Mapped[int | None] = mapped_column(Integer)
    seat_type: Mapped[str | None] = mapped_column(String(20))  # e.g. "window", "aisle", "middle"
    price_modifier: Mapped[float] = mapped_column(Numeric(12, 2), default=0, server_default="0")
    status: Mapped[str] = mapped_column(
        Enum("available", "locked", "booked", name="seat_status"),
        default="available",
        server_default="available",
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )

    trip: Mapped["Trip"] = relationship(back_populates="seats")


class TripIncident(Base):
    __tablename__ = "trip_incidents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    trip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False
    )
    driver_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(10), default="low", server_default="low")
    reported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_notes: Mapped[str | None] = mapped_column(Text)

    trip: Mapped["Trip"] = relationship(back_populates="incidents")
    driver: Mapped["Driver"] = relationship()  # noqa: F821
