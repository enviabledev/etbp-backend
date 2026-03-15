import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.core.constants import BookingStatus, GenderType
from app.schemas.common import BaseSchema


class PassengerInput(BaseModel):
    seat_id: uuid.UUID
    first_name: str = Field(..., max_length=100)
    last_name: str = Field(..., max_length=100)
    gender: GenderType | None = None
    phone: str | None = Field(None, max_length=20)
    is_primary: bool = False


class CreateBookingRequest(BaseModel):
    trip_id: uuid.UUID
    passengers: list[PassengerInput] = Field(..., min_length=1)
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(None, max_length=20)
    emergency_contact_name: str | None = Field(None, max_length=200)
    emergency_contact_phone: str | None = Field(None, max_length=20)
    special_requests: str | None = None
    promo_code: str | None = None


class BookingPassengerResponse(BaseSchema):
    id: uuid.UUID
    seat_id: uuid.UUID
    first_name: str
    last_name: str
    gender: GenderType | None
    phone: str | None
    is_primary: bool
    checked_in: bool
    qr_code_data: str | None


class BookingResponse(BaseSchema):
    id: uuid.UUID
    reference: str
    user_id: uuid.UUID
    trip_id: uuid.UUID
    status: BookingStatus
    total_amount: float
    currency: str
    passenger_count: int
    contact_email: str | None
    contact_phone: str | None
    special_requests: str | None
    created_at: datetime


class BookingDetailResponse(BookingResponse):
    passengers: list[BookingPassengerResponse] = []
    emergency_contact_name: str | None
    emergency_contact_phone: str | None
    cancellation_reason: str | None
    cancelled_at: datetime | None
    checked_in_at: datetime | None


class CancelBookingRequest(BaseModel):
    reason: str | None = None


class CancelBookingResponse(BaseSchema):
    id: uuid.UUID
    reference: str
    status: BookingStatus
    cancellation_reason: str | None
    cancelled_at: datetime | None
    refund_amount: float | None
    refund_percentage: int | None


class RescheduleRequest(BaseModel):
    new_trip_id: uuid.UUID
    new_seat_ids: list[uuid.UUID] = Field(..., min_length=1)


class ApplyPromoRequest(BaseModel):
    promo_code: str = Field(..., max_length=50)


class ApplyPromoResponse(BaseSchema):
    booking_reference: str
    original_amount: float
    discount_amount: float
    new_total: float
    promo_code: str
