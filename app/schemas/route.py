import uuid
from datetime import time

from pydantic import BaseModel, Field

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
