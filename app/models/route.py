import uuid
from datetime import datetime, time, timezone

from sqlalchemy import (
    Boolean, DateTime, Float, Integer, Numeric, String, Text, Time,
    ForeignKey, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.user import Base, TimestampMixin


class Terminal(TimestampMixin, Base):
    __tablename__ = "terminals"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(100), nullable=False)
    country: Mapped[str] = mapped_column(String(100), default="Nigeria", server_default="Nigeria")
    address: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    phone: Mapped[str | None] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    amenities: Mapped[dict | None] = mapped_column(JSON)
    opening_time: Mapped[time | None] = mapped_column(Time)
    closing_time: Mapped[time | None] = mapped_column(Time)

    origin_routes: Mapped[list["Route"]] = relationship(
        back_populates="origin_terminal", foreign_keys="Route.origin_terminal_id"
    )
    destination_routes: Mapped[list["Route"]] = relationship(
        back_populates="destination_terminal", foreign_keys="Route.destination_terminal_id"
    )


class Route(TimestampMixin, Base):
    __tablename__ = "routes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    origin_terminal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("terminals.id"), nullable=False
    )
    destination_terminal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("terminals.id"), nullable=False
    )
    distance_km: Mapped[float | None] = mapped_column(Float)
    estimated_duration_minutes: Mapped[int | None] = mapped_column(Integer)
    base_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="NGN", server_default="NGN")
    luggage_policy: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    extra_luggage_price: Mapped[float | None] = mapped_column(Numeric(12, 2))

    origin_terminal: Mapped["Terminal"] = relationship(
        back_populates="origin_routes", foreign_keys=[origin_terminal_id]
    )
    destination_terminal: Mapped["Terminal"] = relationship(
        back_populates="destination_routes", foreign_keys=[destination_terminal_id]
    )
    stops: Mapped[list["RouteStop"]] = relationship(
        back_populates="route", cascade="all, delete-orphan", order_by="RouteStop.stop_order"
    )
    schedules: Mapped[list["Schedule"]] = relationship(back_populates="route")  # noqa: F821
    trips: Mapped[list["Trip"]] = relationship(back_populates="route")  # noqa: F821


class RouteStop(Base):
    __tablename__ = "route_stops"
    __table_args__ = (
        UniqueConstraint("route_id", "stop_order", name="uq_route_stop_order"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    route_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("routes.id", ondelete="CASCADE"), nullable=False
    )
    terminal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("terminals.id"), nullable=False
    )
    stop_order: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_from_origin_minutes: Mapped[int | None] = mapped_column(Integer)
    price_from_origin: Mapped[float | None] = mapped_column(Numeric(12, 2))
    is_pickup_point: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    is_dropoff_point: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    route: Mapped["Route"] = relationship(back_populates="stops")
    terminal: Mapped["Terminal"] = relationship()
