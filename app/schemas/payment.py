import uuid
from datetime import datetime

from pydantic import BaseModel

from app.core.constants import PaymentMethod, PaymentStatus
from app.schemas.common import BaseSchema


class InitiatePaymentRequest(BaseModel):
    booking_id: uuid.UUID
    method: PaymentMethod
    callback_url: str | None = None


class PaymentResponse(BaseSchema):
    id: uuid.UUID
    booking_id: uuid.UUID
    amount: float
    currency: str
    method: PaymentMethod
    status: PaymentStatus
    gateway: str | None
    gateway_reference: str | None
    paid_at: datetime | None
    created_at: datetime


class PaystackWebhookPayload(BaseModel):
    event: str
    data: dict
