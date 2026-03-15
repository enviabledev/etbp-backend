import uuid
from datetime import date

from pydantic import BaseModel

from app.core.constants import VehicleStatus
from app.schemas.common import BaseSchema


class VehicleTypeResponse(BaseSchema):
    id: uuid.UUID
    name: str
    description: str | None
    seat_capacity: int
    seat_layout: dict | None
    amenities: dict | None


class VehicleResponse(BaseSchema):
    id: uuid.UUID
    vehicle_type: VehicleTypeResponse
    plate_number: str
    make: str | None
    model: str | None
    year: int | None
    color: str | None
    status: VehicleStatus
    current_mileage: float | None
    insurance_expiry: date | None
