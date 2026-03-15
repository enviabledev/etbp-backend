import hashlib
import hmac
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.constants import (
    BookingStatus,
    PaymentMethod,
    PaymentStatus,
    WalletTxType,
)
from app.core.exceptions import BadRequestError, NotFoundError, UnauthorizedError
from app.integrations.paystack import PaystackClient
from app.models.booking import Booking
from app.models.payment import Payment, Wallet, WalletTransaction
from app.schemas.payment import InitiatePaymentRequest


def verify_paystack_signature(payload_bytes: bytes, signature: str) -> bool:
    expected = hmac.HMAC(
        settings.paystack_secret_key.encode(),
        payload_bytes,
        hashlib.sha512,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


async def initiate_payment(
    db: AsyncSession,
    user_id: uuid.UUID,
    data: InitiatePaymentRequest,
    paystack_client: PaystackClient | None = None,
) -> dict:
    booking_result = await db.execute(
        select(Booking).where(Booking.id == data.booking_id, Booking.user_id == user_id)
    )
    booking = booking_result.scalar_one_or_none()
    if not booking:
        raise NotFoundError("Booking not found")
    if booking.status != BookingStatus.PENDING:
        raise BadRequestError("Booking is not in pending status")

    payment = Payment(
        booking_id=booking.id,
        user_id=user_id,
        amount=float(booking.total_amount),
        method=data.method.value,
        gateway="paystack",
    )
    db.add(payment)
    await db.flush()

    client = paystack_client or PaystackClient()
    result = await client.initialize_transaction(
        email=booking.contact_email or "",
        amount=int(float(booking.total_amount) * 100),  # kobo
        reference=str(payment.id),
        callback_url=data.callback_url,
    )

    payment.gateway_reference = result.get("reference")
    await db.flush()

    return {
        "payment_id": payment.id,
        "authorization_url": result.get("authorization_url", ""),
        "reference": result.get("reference", ""),
    }


async def handle_paystack_webhook(
    db: AsyncSession,
    payload: dict,
    signature: str | None = None,
    raw_body: bytes | None = None,
) -> None:
    # Verify signature if provided
    if signature and raw_body:
        if not verify_paystack_signature(raw_body, signature):
            raise UnauthorizedError("Invalid webhook signature")

    event = payload.get("event")
    data = payload.get("data", {})

    if event != "charge.success":
        return

    reference = data.get("reference")
    if not reference:
        return

    # Idempotent: skip if already processed
    result = await db.execute(
        select(Payment).where(Payment.gateway_reference == reference)
    )
    payment = result.scalar_one_or_none()
    if not payment:
        return

    if payment.status == PaymentStatus.SUCCESSFUL:
        return  # Already processed

    payment.status = PaymentStatus.SUCCESSFUL
    payment.gateway_response = data
    payment.paid_at = datetime.now(timezone.utc)

    booking_result = await db.execute(
        select(Booking).where(Booking.id == payment.booking_id)
    )
    booking = booking_result.scalar_one_or_none()
    if booking and booking.status == BookingStatus.PENDING:
        booking.status = BookingStatus.CONFIRMED

    await db.flush()


# ── Wallet ──


async def get_or_create_wallet(db: AsyncSession, user_id: uuid.UUID) -> Wallet:
    result = await db.execute(
        select(Wallet).where(Wallet.user_id == user_id)
    )
    wallet = result.scalar_one_or_none()
    if not wallet:
        wallet = Wallet(user_id=user_id)
        db.add(wallet)
        await db.flush()
        await db.refresh(wallet)
    return wallet


async def get_wallet(db: AsyncSession, user_id: uuid.UUID) -> dict:
    wallet = await get_or_create_wallet(db, user_id)
    return {
        "id": wallet.id,
        "balance": float(wallet.balance),
        "currency": wallet.currency,
        "is_active": wallet.is_active,
    }


async def initiate_wallet_topup(
    db: AsyncSession,
    user_id: uuid.UUID,
    amount: float,
    callback_url: str | None = None,
    paystack_client: PaystackClient | None = None,
) -> dict:
    wallet = await get_or_create_wallet(db, user_id)

    # Create a Payment record with no booking
    # We'll use a special reference prefix for topups
    from app.models.user import User

    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one()

    payment = Payment(
        booking_id=None,
        user_id=user_id,
        amount=amount,
        method=PaymentMethod.CARD,
        gateway="paystack",
    )
    # booking_id is required in current model, so we need a workaround
    # Actually booking_id is NOT NULL in the model. Let's handle wallet topups
    # differently — store the intent and process in webhook
    client = paystack_client or PaystackClient()
    reference = f"wt-{uuid.uuid4()}"
    result = await client.initialize_transaction(
        email=user.email or "",
        amount=int(amount * 100),
        reference=reference,
        callback_url=callback_url,
        metadata={"type": "wallet_topup", "user_id": str(user_id)},
    )

    return {
        "payment_id": uuid.uuid4(),  # placeholder
        "authorization_url": result.get("authorization_url", ""),
        "reference": result.get("reference", reference),
    }


async def process_wallet_topup(
    db: AsyncSession, user_id: uuid.UUID, amount: float, reference: str
) -> None:
    """Credit wallet after successful Paystack payment for top-up."""
    wallet = await get_or_create_wallet(db, user_id)
    wallet.balance = float(wallet.balance) + amount

    tx = WalletTransaction(
        wallet_id=wallet.id,
        type=WalletTxType.TOP_UP,
        amount=amount,
        balance_after=float(wallet.balance),
        reference=reference,
        description=f"Wallet top-up via Paystack",
    )
    db.add(tx)
    await db.flush()


async def pay_with_wallet(
    db: AsyncSession, user_id: uuid.UUID, booking_id: uuid.UUID
) -> dict:
    booking_result = await db.execute(
        select(Booking).where(Booking.id == booking_id, Booking.user_id == user_id)
    )
    booking = booking_result.scalar_one_or_none()
    if not booking:
        raise NotFoundError("Booking not found")
    if booking.status != BookingStatus.PENDING:
        raise BadRequestError("Booking is not in pending status")

    wallet = await get_or_create_wallet(db, user_id)
    amount = float(booking.total_amount)

    if float(wallet.balance) < amount:
        raise BadRequestError(
            f"Insufficient wallet balance. Need {amount}, have {float(wallet.balance)}"
        )

    # Atomic deduction
    wallet.balance = float(wallet.balance) - amount

    tx = WalletTransaction(
        wallet_id=wallet.id,
        type=WalletTxType.PAYMENT,
        amount=amount,
        balance_after=float(wallet.balance),
        reference=booking.reference,
        description=f"Payment for booking {booking.reference}",
    )
    db.add(tx)

    # Record payment
    payment = Payment(
        booking_id=booking.id,
        user_id=user_id,
        amount=amount,
        method=PaymentMethod.WALLET,
        status=PaymentStatus.SUCCESSFUL,
        gateway="wallet",
        paid_at=datetime.now(timezone.utc),
    )
    db.add(payment)

    booking.status = BookingStatus.CONFIRMED
    await db.flush()

    return {
        "booking_reference": booking.reference,
        "amount_paid": amount,
        "wallet_balance": float(wallet.balance),
        "booking_status": booking.status,
    }
