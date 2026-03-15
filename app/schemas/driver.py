import uuid
from datetime import date

from app.schemas.common import BaseSchema
from app.schemas.user import UserResponse


class DriverResponse(BaseSchema):
    id: uuid.UUID
    user: UserResponse
    license_number: str
    license_expiry: date
    license_class: str | None
    years_experience: int | None
    rating_avg: float
    total_trips: int
    is_available: bool
