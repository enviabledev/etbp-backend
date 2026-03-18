import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, String, Text, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(20), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    first_name: Mapped[str | None] = mapped_column(String(100))
    last_name: Mapped[str | None] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(
        Enum(
            "passenger", "agent", "driver", "fleet_manager", "admin", "super_admin",
            name="user_role",
        ),
        default="passenger",
        server_default="passenger",
    )
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    date_of_birth: Mapped[date | None] = mapped_column()
    gender: Mapped[str | None] = mapped_column(
        Enum("male", "female", "other", name="gender_type")
    )
    emergency_contact_name: Mapped[str | None] = mapped_column(String(200))
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(20))
    google_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    apple_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    phone_verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    has_logged_in: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigned_terminal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("terminals.id"), nullable=True
    )

    # Relationships
    assigned_terminal: Mapped["Terminal | None"] = relationship()  # noqa: F821
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    bookings: Mapped[list["Booking"]] = relationship(  # noqa: F821
        back_populates="user", foreign_keys="Booking.user_id"
    )
    wallet: Mapped["Wallet | None"] = relationship(back_populates="user")  # noqa: F821
    notifications: Mapped[list["Notification"]] = relationship(back_populates="user")  # noqa: F821

    @property
    def full_name(self) -> str:
        parts = [self.first_name, self.last_name]
        return " ".join(p for p in parts if p) or ""


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    user: Mapped["User"] = relationship(back_populates="refresh_tokens")


class OTPCode(Base):
    __tablename__ = "otp_codes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    phone: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(255))
    code: Mapped[str] = mapped_column(String(10), nullable=False)
    purpose: Mapped[str] = mapped_column(
        Enum(
            "email_verification", "phone_verification", "password_reset", "login",
            name="otp_purpose",
        ),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
