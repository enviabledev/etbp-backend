import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, String, Text, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.user import Base


class CorporateAccount(Base):
    __tablename__ = "corporate_accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    company_email: Mapped[str] = mapped_column(String(200), nullable=False)
    company_phone: Mapped[str | None] = mapped_column(String(50))
    company_address: Mapped[str | None] = mapped_column(Text)
    registration_number: Mapped[str | None] = mapped_column(String(100))
    tax_id: Mapped[str | None] = mapped_column(String(100))
    contact_person_name: Mapped[str | None] = mapped_column(String(200))
    contact_person_email: Mapped[str | None] = mapped_column(String(200))
    contact_person_phone: Mapped[str | None] = mapped_column(String(50))
    credit_limit: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    current_balance: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0, server_default="0")
    billing_cycle: Mapped[str] = mapped_column(String(20), nullable=False, default="monthly", server_default="monthly")
    billing_day: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    payment_terms_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30, server_default="30")
    discount_percentage: Mapped[float] = mapped_column(Numeric(5, 2), default=0, server_default="0")
    rate_agreement: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", server_default="active")
    suspended_reason: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    employees: Mapped[list["CorporateEmployee"]] = relationship(back_populates="corporate_account", cascade="all, delete-orphan")


class CorporateEmployee(Base):
    __tablename__ = "corporate_employees"
    __table_args__ = (UniqueConstraint("corporate_account_id", "user_id", name="uq_corp_employee"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    corporate_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("corporate_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    employee_id: Mapped[str | None] = mapped_column(String(50))
    department: Mapped[str | None] = mapped_column(String(100))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    added_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    corporate_account: Mapped["CorporateAccount"] = relationship(back_populates="employees")
    user: Mapped["User"] = relationship(foreign_keys=[user_id])  # noqa: F821


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    corporate_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("corporate_accounts.id"), nullable=False, index=True)
    billing_period_start: Mapped[date] = mapped_column(Date, nullable=False)
    billing_period_end: Mapped[date] = mapped_column(Date, nullable=False)
    subtotal: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    discount_amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    tax_amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    total_amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", server_default="draft", index=True)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    paid_amount: Mapped[float] = mapped_column(Numeric(14, 2), default=0, server_default="0")
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payment_reference: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    line_items: Mapped[dict | None] = mapped_column(JSON)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    corporate_account: Mapped["CorporateAccount"] = relationship()
