import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.constants import UserRole
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.dependencies import CurrentUser, DBSession, require_role
from app.models.booking import Booking
from app.models.corporate import CorporateAccount, CorporateEmployee, Invoice
from app.models.user import User
from app.services.audit_service import log_action

router = APIRouter(prefix="/corporate", tags=["Admin - Corporate"])
AdminUser = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))


class CreateAccountRequest(BaseModel):
    company_name: str = Field(..., max_length=200)
    company_email: str
    company_phone: str | None = None
    company_address: str | None = None
    registration_number: str | None = None
    tax_id: str | None = None
    contact_person_name: str | None = None
    contact_person_email: str | None = None
    contact_person_phone: str | None = None
    credit_limit: float = 0
    billing_cycle: str = "monthly"
    billing_day: int = 1
    payment_terms_days: int = 30
    discount_percentage: float = 0
    rate_agreement: list | None = None


class UpdateStatusRequest(BaseModel):
    status: str
    reason: str | None = None


class AddEmployeeRequest(BaseModel):
    user_id: uuid.UUID | None = None
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    employee_id: str | None = None
    department: str | None = None
    is_admin: bool = False


class GenerateInvoiceRequest(BaseModel):
    corporate_account_id: uuid.UUID
    period_start: date
    period_end: date


class RecordPaymentRequest(BaseModel):
    amount: float
    payment_reference: str | None = None
    notes: str | None = None


# ── Corporate Accounts ──

@router.get("/accounts", dependencies=[AdminUser])
async def list_accounts(db: DBSession, status: str | None = None, search: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    query = select(CorporateAccount)
    if status:
        query = query.where(CorporateAccount.status == status)
    if search:
        query = query.where(CorporateAccount.company_name.ilike(f"%{search}%"))
    count_q = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_q.scalar() or 0
    query = query.order_by(CorporateAccount.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    items = []
    for a in result.scalars().all():
        utilization = (float(a.current_balance) / float(a.credit_limit) * 100) if a.credit_limit and float(a.credit_limit) > 0 else 0
        items.append({
            "id": str(a.id), "company_name": a.company_name, "company_email": a.company_email,
            "contact_person_name": a.contact_person_name, "credit_limit": float(a.credit_limit),
            "current_balance": float(a.current_balance), "utilization": round(utilization, 1),
            "discount_percentage": float(a.discount_percentage), "status": a.status,
            "billing_cycle": a.billing_cycle, "created_at": str(a.created_at),
        })
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/accounts/{account_id}", dependencies=[AdminUser])
async def get_account(account_id: uuid.UUID, db: DBSession):
    result = await db.execute(
        select(CorporateAccount).options(selectinload(CorporateAccount.employees).selectinload(CorporateEmployee.user))
        .where(CorporateAccount.id == account_id)
    )
    a = result.scalar_one_or_none()
    if not a:
        raise NotFoundError("Corporate account not found")
    utilization = (float(a.current_balance) / float(a.credit_limit) * 100) if a.credit_limit and float(a.credit_limit) > 0 else 0
    employees = [
        {"id": str(e.id), "user_id": str(e.user_id), "name": f"{e.user.first_name} {e.user.last_name}" if e.user else "", "email": e.user.email if e.user else "", "employee_id": e.employee_id, "department": e.department, "is_admin": e.is_admin, "is_active": e.is_active}
        for e in a.employees
    ]
    return {
        "id": str(a.id), "company_name": a.company_name, "company_email": a.company_email,
        "company_phone": a.company_phone, "company_address": a.company_address,
        "registration_number": a.registration_number, "tax_id": a.tax_id,
        "contact_person_name": a.contact_person_name, "contact_person_email": a.contact_person_email,
        "contact_person_phone": a.contact_person_phone,
        "credit_limit": float(a.credit_limit), "current_balance": float(a.current_balance),
        "available_credit": float(a.credit_limit) - float(a.current_balance),
        "utilization": round(utilization, 1),
        "billing_cycle": a.billing_cycle, "billing_day": a.billing_day,
        "payment_terms_days": a.payment_terms_days, "discount_percentage": float(a.discount_percentage),
        "rate_agreement": a.rate_agreement, "status": a.status, "notes": a.notes,
        "employees": employees, "created_at": str(a.created_at),
    }


@router.post("/accounts", status_code=201, dependencies=[AdminUser])
async def create_account(data: CreateAccountRequest, db: DBSession, current_user: CurrentUser):
    account = CorporateAccount(**data.model_dump(), created_by=current_user.id)
    db.add(account)
    await db.flush()
    await log_action(db, current_user.id, "create_corporate_account", "corporate_account", str(account.id), {"company": data.company_name})
    return {"id": str(account.id), "company_name": account.company_name, "status": account.status}


@router.put("/accounts/{account_id}", dependencies=[AdminUser])
async def update_account(account_id: uuid.UUID, data: CreateAccountRequest, db: DBSession, current_user: CurrentUser):
    result = await db.execute(select(CorporateAccount).where(CorporateAccount.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise NotFoundError("Account not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(account, field, value)
    await db.flush()
    return {"id": str(account.id), "updated": True}


@router.patch("/accounts/{account_id}/status", dependencies=[AdminUser])
async def update_account_status(account_id: uuid.UUID, data: UpdateStatusRequest, db: DBSession, current_user: CurrentUser):
    result = await db.execute(select(CorporateAccount).where(CorporateAccount.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise NotFoundError("Account not found")
    account.status = data.status
    if data.reason:
        account.suspended_reason = data.reason
    await db.flush()
    await log_action(db, current_user.id, "update_corporate_status", "corporate_account", str(account_id), {"status": data.status})
    return {"id": str(account.id), "status": account.status}


# ── Employees ──

@router.get("/accounts/{account_id}/employees", dependencies=[AdminUser])
async def list_employees(account_id: uuid.UUID, db: DBSession):
    result = await db.execute(
        select(CorporateEmployee).options(selectinload(CorporateEmployee.user))
        .where(CorporateEmployee.corporate_account_id == account_id)
    )
    return [
        {"id": str(e.id), "user_id": str(e.user_id), "name": f"{e.user.first_name} {e.user.last_name}" if e.user else "", "email": e.user.email if e.user else "", "phone": e.user.phone if e.user else "", "employee_id": e.employee_id, "department": e.department, "is_admin": e.is_admin, "is_active": e.is_active}
        for e in result.scalars().all()
    ]


@router.post("/accounts/{account_id}/employees", status_code=201, dependencies=[AdminUser])
async def add_employee(account_id: uuid.UUID, data: AddEmployeeRequest, db: DBSession, current_user: CurrentUser):
    # Verify account exists
    acc_q = await db.execute(select(CorporateAccount.id).where(CorporateAccount.id == account_id))
    if not acc_q.scalar():
        raise NotFoundError("Account not found")

    user_id = data.user_id
    if not user_id and data.email:
        # Find or create user
        user_q = await db.execute(select(User).where(User.email == data.email.lower()))
        user = user_q.scalar_one_or_none()
        if not user:
            from app.core.security import hash_password
            user = User(email=data.email.lower(), first_name=data.first_name or "", last_name=data.last_name or "", phone=data.phone, role="passenger", is_active=True, has_logged_in=False, created_by=current_user.id, password_hash=hash_password(uuid.uuid4().hex[:16]))
            db.add(user)
            await db.flush()
            await db.refresh(user)
        user_id = user.id

    if not user_id:
        raise BadRequestError("Provide user_id or email")

    # Check duplicate
    existing = await db.execute(select(CorporateEmployee).where(CorporateEmployee.corporate_account_id == account_id, CorporateEmployee.user_id == user_id))
    if existing.scalar_one_or_none():
        raise ConflictError("Employee already linked to this account")

    emp = CorporateEmployee(corporate_account_id=account_id, user_id=user_id, employee_id=data.employee_id, department=data.department, is_admin=data.is_admin, added_by=current_user.id)
    db.add(emp)
    await db.flush()
    return {"id": str(emp.id), "user_id": str(user_id)}


@router.delete("/accounts/{account_id}/employees/{employee_id}", dependencies=[AdminUser])
async def remove_employee(account_id: uuid.UUID, employee_id: uuid.UUID, db: DBSession):
    result = await db.execute(select(CorporateEmployee).where(CorporateEmployee.id == employee_id, CorporateEmployee.corporate_account_id == account_id))
    emp = result.scalar_one_or_none()
    if not emp:
        raise NotFoundError("Employee not found")
    await db.delete(emp)
    await db.flush()
    return {"removed": True}


# ── Invoices ──

@router.get("/invoices", dependencies=[AdminUser])
async def list_invoices(db: DBSession, corporate_account_id: uuid.UUID | None = None, status: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    query = select(Invoice).options(selectinload(Invoice.corporate_account))
    if corporate_account_id:
        query = query.where(Invoice.corporate_account_id == corporate_account_id)
    if status:
        query = query.where(Invoice.status == status)
    count_q = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_q.scalar() or 0
    query = query.order_by(Invoice.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    return {
        "items": [
            {"id": str(i.id), "invoice_number": i.invoice_number, "company_name": i.corporate_account.company_name if i.corporate_account else "", "period_start": str(i.billing_period_start), "period_end": str(i.billing_period_end), "total_amount": float(i.total_amount), "paid_amount": float(i.paid_amount), "status": i.status, "due_date": str(i.due_date), "created_at": str(i.created_at)}
            for i in result.scalars().all()
        ],
        "total": total, "page": page, "page_size": page_size,
    }


@router.get("/invoices/{invoice_id}", dependencies=[AdminUser])
async def get_invoice(invoice_id: uuid.UUID, db: DBSession):
    result = await db.execute(select(Invoice).options(selectinload(Invoice.corporate_account)).where(Invoice.id == invoice_id))
    inv = result.scalar_one_or_none()
    if not inv:
        raise NotFoundError("Invoice not found")
    return {
        "id": str(inv.id), "invoice_number": inv.invoice_number,
        "company_name": inv.corporate_account.company_name if inv.corporate_account else "",
        "period_start": str(inv.billing_period_start), "period_end": str(inv.billing_period_end),
        "subtotal": float(inv.subtotal), "discount_amount": float(inv.discount_amount),
        "tax_amount": float(inv.tax_amount), "total_amount": float(inv.total_amount),
        "paid_amount": float(inv.paid_amount), "status": inv.status,
        "due_date": str(inv.due_date), "payment_reference": inv.payment_reference,
        "line_items": inv.line_items or [], "notes": inv.notes,
        "sent_at": str(inv.sent_at) if inv.sent_at else None,
        "created_at": str(inv.created_at),
    }


@router.post("/invoices/generate", status_code=201, dependencies=[AdminUser])
async def generate_invoice(data: GenerateInvoiceRequest, db: DBSession, current_user: CurrentUser):
    # Get account
    acc_q = await db.execute(select(CorporateAccount).where(CorporateAccount.id == data.corporate_account_id))
    account = acc_q.scalar_one_or_none()
    if not account:
        raise NotFoundError("Account not found")

    # Find bookings in period
    from app.models.schedule import Trip
    from app.models.route import Route
    bookings_q = await db.execute(
        select(Booking).options(
            selectinload(Booking.trip).selectinload(Trip.route),
            selectinload(Booking.passengers),
        ).where(
            Booking.corporate_account_id == data.corporate_account_id,
            func.date(Booking.created_at) >= data.period_start,
            func.date(Booking.created_at) <= data.period_end,
            Booking.status.in_(["confirmed", "checked_in", "completed"]),
        )
    )
    bookings = bookings_q.scalars().all()

    line_items = []
    subtotal = 0.0
    for b in bookings:
        amt = float(b.total_amount)
        primary = next((p for p in b.passengers if p.is_primary), b.passengers[0] if b.passengers else None)
        line_items.append({
            "booking_ref": b.reference,
            "date": str(b.created_at.date()) if b.created_at else "",
            "route": b.trip.route.name if b.trip and b.trip.route else "",
            "passengers": b.passenger_count,
            "employee_name": f"{primary.first_name} {primary.last_name}" if primary else "",
            "amount": amt,
        })
        subtotal += amt

    discount = round(subtotal * float(account.discount_percentage) / 100, 2) if account.discount_percentage else 0
    total = round(subtotal - discount, 2)

    # Generate invoice number
    year = data.period_end.year
    count_q = await db.execute(select(func.count(Invoice.id)).where(func.extract("year", Invoice.created_at) == year))
    seq = (count_q.scalar() or 0) + 1
    invoice_number = f"INV-{year}-{seq:04d}"

    due_date = data.period_end + timedelta(days=account.payment_terms_days)

    invoice = Invoice(
        invoice_number=invoice_number, corporate_account_id=account.id,
        billing_period_start=data.period_start, billing_period_end=data.period_end,
        subtotal=subtotal, discount_amount=discount, total_amount=total,
        due_date=due_date, line_items=line_items, created_by=current_user.id,
    )
    db.add(invoice)
    await db.flush()
    await log_action(db, current_user.id, "generate_invoice", "invoice", str(invoice.id), {"number": invoice_number, "amount": total})
    return {"id": str(invoice.id), "invoice_number": invoice_number, "total_amount": total, "due_date": str(due_date)}


@router.patch("/invoices/{invoice_id}/payment", dependencies=[AdminUser])
async def record_payment(invoice_id: uuid.UUID, data: RecordPaymentRequest, db: DBSession, current_user: CurrentUser):
    result = await db.execute(select(Invoice).where(Invoice.id == invoice_id))
    inv = result.scalar_one_or_none()
    if not inv:
        raise NotFoundError("Invoice not found")

    inv.paid_amount = float(inv.paid_amount) + data.amount
    if data.payment_reference:
        inv.payment_reference = data.payment_reference
    if data.notes:
        inv.notes = (inv.notes or "") + f"\nPayment: \u20a6{data.amount:,.2f} - {data.notes}"
    inv.paid_at = datetime.now(timezone.utc)

    if float(inv.paid_amount) >= float(inv.total_amount):
        inv.status = "paid"
    else:
        inv.status = "partially_paid"

    # Decrease corporate balance
    acc_q = await db.execute(select(CorporateAccount).where(CorporateAccount.id == inv.corporate_account_id))
    account = acc_q.scalar_one()
    account.current_balance = max(0, float(account.current_balance) - data.amount)

    await db.flush()
    await log_action(db, current_user.id, "record_invoice_payment", "invoice", str(invoice_id), {"amount": data.amount})
    return {"id": str(inv.id), "status": inv.status, "paid_amount": float(inv.paid_amount)}


@router.post("/invoices/{invoice_id}/send", dependencies=[AdminUser])
async def send_invoice(invoice_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    result = await db.execute(select(Invoice).where(Invoice.id == invoice_id))
    inv = result.scalar_one_or_none()
    if not inv:
        raise NotFoundError("Invoice not found")
    inv.status = "sent"
    inv.sent_at = datetime.now(timezone.utc)
    await db.flush()
    return {"sent": True, "sent_at": str(inv.sent_at)}
