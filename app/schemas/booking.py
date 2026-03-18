import uuid
from datetime import date, datetime, time

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from sqlalchemy import inspect as sa_inspect

from app.core.constants import BookingStatus, GenderType
from app.core.validators import validate_international_phone
from app.schemas.common import BaseSchema


class PassengerInput(BaseModel):
    seat_id: uuid.UUID
    first_name: str = Field(..., max_length=100)
    last_name: str = Field(..., max_length=100)
    gender: GenderType | None = None
    phone: str | None = Field(None, max_length=20)
    is_primary: bool = False

    @field_validator("phone")
    @classmethod
    def check_phone(cls, v: str | None) -> str | None:
        return validate_international_phone(v)


class CreateBookingRequest(BaseModel):
    trip_id: uuid.UUID
    passengers: list[PassengerInput] = Field(..., min_length=1)
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(None, max_length=20)
    emergency_contact_name: str | None = Field(None, max_length=200)
    emergency_contact_phone: str | None = Field(None, max_length=20)
    special_requests: str | None = None
    promo_code: str | None = None

    @field_validator("contact_phone", "emergency_contact_phone")
    @classmethod
    def check_phone(cls, v: str | None) -> str | None:
        return validate_international_phone(v)


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
    seat_number: str | None = None
    seat_type: str | None = None

    @model_validator(mode="before")
    @classmethod
    def extract_seat_info(cls, data: object) -> object:
        """Extract seat_number from the related seat object if present."""
        if not hasattr(data, "__table__"):
            return data
        state = sa_inspect(data)
        if "seat" not in state.unloaded and hasattr(data, "seat") and data.seat:
            if not getattr(data, "seat_number", None):
                data.seat_number = data.seat.seat_number  # type: ignore[union-attr]
            if not getattr(data, "seat_type", None):
                data.seat_type = data.seat.seat_type  # type: ignore[union-attr]
        return data


class TerminalBrief(BaseSchema):
    id: uuid.UUID
    name: str
    code: str
    city: str
    state: str


class RouteBrief(BaseSchema):
    id: uuid.UUID
    name: str
    code: str
    origin_terminal: TerminalBrief
    destination_terminal: TerminalBrief


class TripBrief(BaseSchema):
    id: uuid.UUID
    departure_date: date
    departure_time: time
    status: str
    price: float
    route: RouteBrief | None = None
    vehicle_type: dict | None = None


class BookingResponse(BaseSchema):
    id: uuid.UUID
    reference: str
    booking_reference: str | None = None  # alias for frontend compatibility
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
    payment_method_hint: str | None = None
    payment_deadline: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def set_booking_reference(cls, data: object) -> object:
        """Set booking_reference as alias for reference."""
        if hasattr(data, "reference"):
            data.booking_reference = data.reference  # type: ignore[union-attr]
        elif isinstance(data, dict) and "reference" in data:
            data["booking_reference"] = data["reference"]
        return data


class BookingDetailResponse(BookingResponse):
    passengers: list[BookingPassengerResponse] = []
    trip: TripBrief | None = None
    emergency_contact_name: str | None
    emergency_contact_phone: str | None
    cancellation_reason: str | None
    cancelled_at: datetime | None
    checked_in_at: datetime | None
    promo_code: str | None = None
    promo_discount: float = 0
    payment_method: str | None = None

    @model_validator(mode="before")
    @classmethod
    def extract_payment_info(cls, data: object) -> object:
        """Extract payment_method from related payments, trip, passengers — only if loaded."""
        if not hasattr(data, "__table__"):
            return data
        state = sa_inspect(data)
        # payments
        if "payments" not in state.unloaded and hasattr(data, "payments") and data.payments:
            successful = [p for p in data.payments if p.status in ("successful", "completed")]
            if successful:
                data.payment_method = successful[0].method  # type: ignore[union-attr]
        # trip — set to None if not loaded to prevent lazy load
        if "trip" in state.unloaded:
            data.trip = None  # type: ignore[union-attr]
        # passengers — set to empty list if not loaded
        if "passengers" in state.unloaded:
            data.passengers = []  # type: ignore[union-attr]
        return data


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
