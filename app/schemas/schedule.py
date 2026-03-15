import uuid
from datetime import date, datetime, time

from pydantic import BaseModel

from app.core.constants import SeatStatus, TripStatus
from app.schemas.common import BaseSchema


class ScheduleResponse(BaseSchema):
    id: uuid.UUID
    route_id: uuid.UUID
    vehicle_type_id: uuid.UUID
    departure_time: time
    recurrence: str | None
    valid_from: date | None
    valid_until: date | None
    is_active: bool
    price_override: float | None


class TripSeatResponse(BaseSchema):
    id: uuid.UUID
    seat_number: str
    seat_row: int | None
    seat_column: int | None
    seat_type: str | None
    price_modifier: float
    status: SeatStatus


class TripResponse(BaseSchema):
    id: uuid.UUID
    route_id: uuid.UUID
    vehicle_id: uuid.UUID | None
    driver_id: uuid.UUID | None
    departure_date: date
    departure_time: time
    status: TripStatus
    price: float
    available_seats: int
    total_seats: int


class TripDetailResponse(TripResponse):
    seats: list[TripSeatResponse] = []
    actual_departure_at: datetime | None
    actual_arrival_at: datetime | None
    notes: str | None
