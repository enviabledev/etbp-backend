import uuid

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.core.exceptions import NotFoundError
from app.dependencies import CurrentUser, DBSession
from app.models.payment import Payment
from app.schemas.payment import (
    InitiatePaymentRequest,
    InitiatePaymentResponse,
    PaymentResponse,
    WalletPaymentRequest,
    WalletPaymentResponse,
    WalletResponse,
    WalletTopupRequest,
    WalletTopupResponse,
)
from app.services import payment_service

router = APIRouter(prefix="/payments", tags=["Payments"])


@router.post("/initiate", response_model=InitiatePaymentResponse, status_code=201)
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
    raw_body = await request.body()
    signature = request.headers.get("x-paystack-signature", "")
    body = await request.json()
    await payment_service.handle_paystack_webhook(
        db, body, signature=signature, raw_body=raw_body
    )
    return {"status": "ok"}


@router.post("/pay-with-wallet", response_model=WalletPaymentResponse)
async def pay_with_wallet(
    data: WalletPaymentRequest,
    db: DBSession,
    current_user: CurrentUser,
):
    return await payment_service.pay_with_wallet(db, current_user.id, data.booking_id)


# ── Wallet ──


@router.get("/wallet/balance", response_model=WalletResponse)
async def get_wallet(db: DBSession, current_user: CurrentUser):
    return await payment_service.get_wallet(db, current_user.id)


@router.post("/wallet/topup", response_model=WalletTopupResponse, status_code=201)
async def topup_wallet(
    data: WalletTopupRequest,
    db: DBSession,
    current_user: CurrentUser,
):
    return await payment_service.initiate_wallet_topup(
        db, current_user.id, data.amount, data.callback_url
    )
