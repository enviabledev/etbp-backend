import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, Float, Integer, String, Text, ForeignKey
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.user import Base, TimestampMixin


class VehicleType(Base):
    __tablename__ = "vehicle_types"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    seat_capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    seat_layout: Mapped[dict | None] = mapped_column(JSON)
    amenities: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    vehicles: Mapped[list["Vehicle"]] = relationship(back_populates="vehicle_type")
    schedules: Mapped[list["Schedule"]] = relationship(back_populates="vehicle_type")  # noqa: F821


class Vehicle(TimestampMixin, Base):
    __tablename__ = "vehicles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    vehicle_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vehicle_types.id"), nullable=False
    )
    plate_number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    make: Mapped[str | None] = mapped_column(String(100))
    model: Mapped[str | None] = mapped_column(String(100))
    year: Mapped[int | None] = mapped_column(Integer)
    color: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(
        Enum("active", "maintenance", "retired", name="vehicle_status"),
        default="active",
        server_default="active",
    )
    current_mileage: Mapped[float | None] = mapped_column(Float)
    last_service_date: Mapped[date | None] = mapped_column()
    next_service_due: Mapped[date | None] = mapped_column()
    insurance_expiry: Mapped[date | None] = mapped_column()
    registration_expiry: Mapped[date | None] = mapped_column()
    inspection_expiry: Mapped[date | None] = mapped_column()
    notes: Mapped[str | None] = mapped_column(Text)

    vehicle_type: Mapped["VehicleType"] = relationship(back_populates="vehicles")
    trips: Mapped[list["Trip"]] = relationship(back_populates="vehicle")  # noqa: F821
