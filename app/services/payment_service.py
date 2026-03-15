import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import BookingStatus, PaymentStatus
from app.core.exceptions import BadRequestError, NotFoundError
from app.integrations.paystack import PaystackClient
from app.models.booking import Booking
from app.models.payment import Payment
from app.schemas.payment import InitiatePaymentRequest


async def initiate_payment(
    db: AsyncSession, user_id: uuid.UUID, data: InitiatePaymentRequest
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

    # Initialize Paystack transaction
    paystack = PaystackClient()
    result = await paystack.initialize_transaction(
        email=booking.contact_email or "",
        amount=int(float(booking.total_amount) * 100),  # kobo
        reference=str(payment.id),
        callback_url=data.callback_url,
    )

    payment.gateway_reference = result.get("reference")
    await db.flush()

    return {
        "payment_id": str(payment.id),
        "authorization_url": result.get("authorization_url"),
        "reference": result.get("reference"),
    }


async def handle_paystack_webhook(db: AsyncSession, payload: dict) -> None:
    event = payload.get("event")
    data = payload.get("data", {})

    if event == "charge.success":
        reference = data.get("reference")
        if not reference:
            return

        result = await db.execute(
            select(Payment).where(Payment.gateway_reference == reference)
        )
        payment = result.scalar_one_or_none()
        if not payment:
            return

        payment.status = PaymentStatus.SUCCESSFUL
        payment.gateway_response = data
        payment.paid_at = datetime.now(timezone.utc)

        # Update booking status
        booking_result = await db.execute(
            select(Booking).where(Booking.id == payment.booking_id)
        )
        booking = booking_result.scalar_one_or_none()
        if booking:
            booking.status = BookingStatus.CONFIRMED

        await db.flush()
