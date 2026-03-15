import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, Enum, Integer, Numeric, String, Text, ForeignKey,
)
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.user import Base, TimestampMixin


class Payment(TimestampMixin, Base):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    booking_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bookings.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="NGN", server_default="NGN")
    method: Mapped[str] = mapped_column(
        Enum(
            "card", "bank_transfer", "wallet", "cash", "mobile_money",
            name="payment_method",
        ),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        Enum(
            "pending", "successful", "failed", "refunded", "partially_refunded",
            name="payment_status",
        ),
        default="pending",
        server_default="pending",
    )
    gateway: Mapped[str | None] = mapped_column(String(50))  # e.g. "paystack"
    gateway_reference: Mapped[str | None] = mapped_column(String(255), index=True)
    gateway_response: Mapped[dict | None] = mapped_column(JSON)
    refund_amount: Mapped[float | None] = mapped_column(Numeric(12, 2))
    refund_reason: Mapped[str | None] = mapped_column(Text)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    booking: Mapped["Booking"] = relationship(back_populates="payments")  # noqa: F821
    user: Mapped["User"] = relationship()  # noqa: F821


class Wallet(TimestampMixin, Base):
    __tablename__ = "wallets"
    __table_args__ = (
        CheckConstraint("balance >= 0", name="ck_wallet_balance_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        unique=True, nullable=False,
    )
    balance: Mapped[float] = mapped_column(Numeric(12, 2), default=0.0, server_default="0.00")
    currency: Mapped[str] = mapped_column(String(3), default="NGN", server_default="NGN")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    user: Mapped["User"] = relationship(back_populates="wallet")  # noqa: F821
    transactions: Mapped[list["WalletTransaction"]] = relationship(
        back_populates="wallet", order_by="WalletTransaction.created_at.desc()"
    )


class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wallets.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(
        Enum(
            "top_up", "payment", "refund", "referral_bonus", "promo_credit",
            name="wallet_tx_type",
        ),
        nullable=False,
    )
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    balance_after: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    wallet: Mapped["Wallet"] = relationship(back_populates="transactions")


class PromoCode(Base):
    __tablename__ = "promo_codes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    discount_type: Mapped[str] = mapped_column(
        Enum("percentage", "fixed", name="discount_type"), nullable=False
    )
    discount_value: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    max_discount: Mapped[float | None] = mapped_column(Numeric(12, 2))
    min_booking_amount: Mapped[float | None] = mapped_column(Numeric(12, 2))
    usage_limit: Mapped[int | None] = mapped_column(Integer)
    used_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    per_user_limit: Mapped[int | None] = mapped_column(Integer)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applicable_routes: Mapped[dict | None] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
