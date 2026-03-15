import uuid

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.core.exceptions import NotFoundError
from app.dependencies import CurrentUser, DBSession
from app.models.payment import Payment
from app.schemas.payment import InitiatePaymentRequest, PaymentResponse, PaystackWebhookPayload
from app.services import payment_service

router = APIRouter(prefix="/payments", tags=["Payments"])


@router.post("", response_model=dict, status_code=201)
async def initiate_payment(
    data: InitiatePaymentRequest,
    db: DBSession,
    current_user: CurrentUser,
):
    return await payment_service.initiate_payment(db, current_user.id, data)


@router.get("/{payment_id}", response_model=PaymentResponse)
async def get_payment(payment_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    result = await db.execute(
        select(Payment).where(Payment.id == payment_id, Payment.user_id == current_user.id)
    )
    payment = result.scalar_one_or_none()
    if not payment:
        raise NotFoundError("Payment not found")
    return payment


@router.post("/webhook/paystack")
async def paystack_webhook(request: Request, db: DBSession):
    body = await request.json()
    await payment_service.handle_paystack_webhook(db, body)
    return {"status": "ok"}
