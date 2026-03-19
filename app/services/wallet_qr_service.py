import uuid
import json
from datetime import datetime, timezone

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.constants import WalletTxType
from app.core.exceptions import BadRequestError, NotFoundError
from app.models.payment import Wallet, WalletTransaction
from app.services.payment_service import get_or_create_wallet


QR_TTL = 300  # 5 minutes


async def generate_payment_qr(db: AsyncSession, user_id: uuid.UUID, amount: float | None = None) -> dict:
    """Generate a time-limited wallet payment QR token."""
    wallet = await get_or_create_wallet(db, user_id)
    token = uuid.uuid4().hex

    r = aioredis.from_url(settings.redis_url)
    # Invalidate any existing token for this user
    old_key = await r.get(f"wallet_qr_user:{user_id}")
    if old_key:
        await r.delete(f"wallet_qr:{old_key.decode()}")
    # Store new token
    data = json.dumps({"user_id": str(user_id), "amount": amount, "created_at": datetime.now(timezone.utc).isoformat()})
    await r.setex(f"wallet_qr:{token}", QR_TTL, data)
    await r.setex(f"wallet_qr_user:{user_id}", QR_TTL, token)
    await r.aclose()

    return {
        "token": token,
        "qr_data": f"ETBP-PAY:{token}",
        "expires_in": QR_TTL,
        "amount": amount,
        "balance": float(wallet.balance),
    }


async def process_wallet_payment(
    db: AsyncSession,
    agent_user_id: uuid.UUID,
    token: str,
    amount: float,
    description: str | None = None,
    booking_id: uuid.UUID | None = None,
) -> dict:
    """Validate token and debit customer wallet."""
    import logging
    logger = logging.getLogger(__name__)

    # Strip QR prefix if present (belt-and-suspenders — mobile should also strip)
    if token.startswith("ETBP-PAY:"):
        token = token[len("ETBP-PAY:"):]

    r = aioredis.from_url(settings.redis_url)
    raw = await r.get(f"wallet_qr:{token}")
    if not raw:
        await r.aclose()
        logger.warning("Wallet QR token not found: %s...", token[:20])
        raise NotFoundError("Payment token expired or invalid. Ask the customer to generate a new QR code.")

    token_data = json.loads(raw.decode())
    customer_id = uuid.UUID(token_data["user_id"])
    token_amount = token_data.get("amount")

    # Use token amount if set (customer pre-approved)
    final_amount = token_amount if token_amount else amount
    if not final_amount or final_amount <= 0:
        await r.aclose()
        raise BadRequestError("Amount is required. The customer's QR code did not include a pre-set amount and no amount was provided.")

    # Delete token (one-time use)
    await r.delete(f"wallet_qr:{token}")
    await r.delete(f"wallet_qr_user:{customer_id}")
    await r.aclose()

    # Debit wallet
    wallet = await get_or_create_wallet(db, customer_id)
    if float(wallet.balance) < final_amount:
        raise BadRequestError(f"Insufficient wallet balance. Need {final_amount}, have {float(wallet.balance)}")

    wallet.balance = float(wallet.balance) - final_amount

    tx = WalletTransaction(
        wallet_id=wallet.id,
        type=WalletTxType.PAYMENT,
        amount=final_amount,
        balance_after=float(wallet.balance),
        reference=str(booking_id) if booking_id else f"agent-{uuid.uuid4().hex[:8]}",
        description=description or "Counter payment via agent",
    )
    db.add(tx)
    await db.flush()

    # Get customer name
    from app.models.user import User
    user_q = await db.execute(select(User).where(User.id == customer_id))
    user = user_q.scalar_one()

    return {
        "success": True,
        "customer_name": f"{user.first_name} {user.last_name}",
        "amount_debited": final_amount,
        "new_balance": float(wallet.balance),
        "transaction_id": str(tx.id),
    }
