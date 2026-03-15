import uuid
from datetime import date, datetime, time

from pydantic import BaseModel, Field

from app.core.constants import SeatStatus, TripStatus
from app.schemas.common import BaseSchema


class TerminalResponse(BaseSchema):
    id: uuid.UUID
    name: str
    code: str
    city: str
    state: str
    country: str
    address: str | None
    latitude: float | None
    longitude: float | None
    phone: str | None
    is_active: bool
    amenities: dict | None
    opening_time: time | None
    closing_time: time | None


class TerminalBriefResponse(BaseSchema):
    id: uuid.UUID
    name: str
    code: str
    city: str
    state: str


class RouteStopResponse(BaseSchema):
    id: uuid.UUID
    terminal: TerminalResponse
    stop_order: int
    duration_from_origin_minutes: int | None
    price_from_origin: float | None
    is_pickup_point: bool
    is_dropoff_point: bool


class RouteResponse(BaseSchema):
    id: uuid.UUID
    name: str
    code: str
    origin_terminal: TerminalResponse
    destination_terminal: TerminalResponse
    distance_km: float | None
    estimated_duration_minutes: int | None
    base_price: float
    currency: str
    luggage_policy: str | None
    is_active: bool


class RouteDetailResponse(RouteResponse):
    stops: list[RouteStopResponse] = []


class RouteBriefResponse(BaseSchema):
    id: uuid.UUID
    name: str
    code: str
    origin_terminal: TerminalBriefResponse
    destination_terminal: TerminalBriefResponse
    distance_km: float | None
    estimated_duration_minutes: int | None
    base_price: float
    currency: str


class VehicleTypeBriefResponse(BaseSchema):
    id: uuid.UUID
    name: str
    seat_capacity: int
    amenities: dict | None


class TripSearchResult(BaseSchema):
    id: uuid.UUID
    route: RouteBriefResponse
    vehicle_type: VehicleTypeBriefResponse | None
    departure_date: date
    departure_time: time
    status: TripStatus
    price: float
    currency: str = "NGN"
    available_seats: int
    total_seats: int
    estimated_duration_minutes: int | None


class PopularRouteResponse(BaseSchema):
    route: RouteBriefResponse
    booking_count: int
