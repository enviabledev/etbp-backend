import uuid
from datetime import date, datetime, time

from pydantic import BaseModel, Field

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


class SeatMapResponse(BaseSchema):
    trip_id: uuid.UUID
    total_seats: int
    available_seats: int
    seats: list[TripSeatResponse]


class LockSeatsRequest(BaseModel):
    seat_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=10)


class LockSeatsResponse(BaseSchema):
    locked_seats: list[uuid.UUID]
    locked_until: datetime
    message: str
