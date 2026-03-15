import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.core.constants import PaymentMethod, PaymentStatus
from app.schemas.common import BaseSchema


class InitiatePaymentRequest(BaseModel):
    booking_id: uuid.UUID
    method: PaymentMethod
    callback_url: str | None = None


class InitiatePaymentResponse(BaseSchema):
    payment_id: uuid.UUID
    authorization_url: str
    reference: str


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


class WalletResponse(BaseSchema):
    id: uuid.UUID
    balance: float
    currency: str
    is_active: bool


class WalletTransactionResponse(BaseSchema):
    id: uuid.UUID
    type: str
    amount: float
    balance_after: float
    reference: str | None
    description: str | None
    created_at: datetime


class WalletTopupRequest(BaseModel):
    amount: float = Field(..., gt=0)
    callback_url: str | None = None


class WalletTopupResponse(BaseSchema):
    payment_id: uuid.UUID
    authorization_url: str
    reference: str


class WalletPaymentRequest(BaseModel):
    booking_id: uuid.UUID


class WalletPaymentResponse(BaseSchema):
    booking_reference: str
    amount_paid: float
    wallet_balance: float
    booking_status: str
