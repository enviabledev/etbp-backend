import io
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import qrcode
from PIL import Image, ImageDraw, ImageFont

from app.core.exceptions import ForbiddenError, NotFoundError
from app.models.booking import Booking, BookingPassenger
from app.models.route import Route
from app.models.schedule import Trip


async def generate_eticket_pdf(
    db: AsyncSession, reference: str, user_id: uuid.UUID
) -> bytes:
    result = await db.execute(
        select(Booking)
        .options(
            selectinload(Booking.passengers).selectinload(BookingPassenger.seat),
            selectinload(Booking.trip).selectinload(Trip.route).selectinload(Route.origin_terminal),
            selectinload(Booking.trip).selectinload(Trip.route).selectinload(Route.destination_terminal),
            selectinload(Booking.payments),
        )
        .where(Booking.reference == reference.upper())
    )
    booking = result.scalar_one_or_none()
    if not booking:
        raise NotFoundError("Booking not found")
    if booking.user_id != user_id:
        raise ForbiddenError("Access denied")

    trip = booking.trip
    route = trip.route

    # Determine payment method from successful payments
    payment_method = "Terminal"
    if booking.payments:
        successful = [p for p in booking.payments if p.status in ("successful", "completed")]
        if successful:
            payment_method = successful[0].method or "Card"

    # Build one receipt image per passenger, then combine into PDF pages
    pages: list[Image.Image] = []

    for passenger in booking.passengers:
        seat_number = str(passenger.seat.seat_number) if passenger.seat else "N/A"
        page = _render_receipt(
            reference=booking.reference,
            passenger_name=f"{passenger.first_name} {passenger.last_name}",
            passenger_phone=passenger.phone or "",
            seat_number=seat_number,
            origin_terminal=route.origin_terminal.name,
            origin_city=route.origin_terminal.city,
            dest_terminal=route.destination_terminal.name,
            dest_city=route.destination_terminal.city,
            date=trip.departure_date.strftime("%d %b %Y"),
            time=trip.departure_time.strftime("%H:%M"),
            qr_data=passenger.qr_code_data or booking.reference,
            amount=float(booking.total_amount),
            currency=booking.currency,
            status=booking.status.upper() if isinstance(booking.status, str) else str(booking.status),
            payment_method=payment_method.capitalize(),
        )
        pages.append(page)

    # Convert to PDF bytes
    pdf_buffer = io.BytesIO()
    if len(pages) == 1:
        pages[0].save(pdf_buffer, "PDF", resolution=96)
    else:
        pages[0].save(
            pdf_buffer, "PDF", resolution=96, save_all=True, append_images=pages[1:]
        )
    pdf_buffer.seek(0)
    return pdf_buffer.read()


def _render_receipt(
    reference: str,
    passenger_name: str,
    passenger_phone: str,
    seat_number: str,
    origin_terminal: str,
    origin_city: str,
    dest_terminal: str,
    dest_city: str,
    date: str,
    time: str,
    qr_data: str,
    amount: float,
    currency: str,
    status: str,
    payment_method: str,
) -> Image.Image:
    """Render a receipt-style e-ticket (80mm thermal printer width)."""
    W = 302  # 80mm at 96 DPI
    PAD = 16
    TEXT_W = W - PAD * 2

    # Fonts
    try:
        font_bold = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 12)
        font_reg = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
        font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 9)
        font_ref = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
        font_header = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 11)
    except (OSError, IOError):
        font_bold = ImageFont.load_default()
        font_reg = font_bold
        font_sm = font_bold
        font_ref = font_bold
        font_header = font_bold

    # Format amount with currency symbol
    symbols = {"NGN": "\u20a6", "USD": "$", "GBP": "\u00a3", "EUR": "\u20ac"}
    symbol = symbols.get(currency, currency + " ")
    amount_str = f"{symbol}{amount:,.2f}"

    # --- Pre-calculate height ---
    y = 0
    y += 50   # header
    y += 30   # "E-TICKET" label + line
    y += 155  # QR code area (10 pad + 130 qr + 15 "scan" text)
    y += 30   # reference
    y += 12   # separator
    # Trip details: 5 rows × 20px
    y += 110
    y += 12   # separator
    # Passenger: 1-2 rows
    y += 25
    if passenger_phone:
        y += 20
    y += 12   # separator
    # Payment: 3 rows
    y += 70
    y += 12   # separator
    y += 40   # footer
    y += 10   # bottom margin
    H = y

    img = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)
    cy = 0  # current y cursor

    # --- 1. HEADER BAR ---
    draw.rectangle([(0, 0), (W, 50)], fill="#1a237e")
    _draw_centered(draw, "ENVIABLE TRANSPORT", W, 16, font_header, "white")
    cy = 50

    # --- 2. E-TICKET label ---
    cy += 8
    _draw_centered(draw, "E - T I C K E T", W, cy, font_sm, "#888888")
    cy += 16
    draw.line([(PAD, cy), (W - PAD, cy)], fill="#dddddd")
    cy += 8

    # --- 3. QR CODE ---
    qr = qrcode.make(f"ETBP-{reference}", box_size=3, border=2)
    qr_size = 130
    qr_img = qr.resize((qr_size, qr_size))
    qr_x = (W - qr_size) // 2
    img.paste(qr_img, (qr_x, cy))
    cy += qr_size + 4
    _draw_centered(draw, "Scan to verify", W, cy, font_sm, "#aaaaaa")
    cy += 16

    # --- 4. BOOKING REFERENCE ---
    _draw_centered(draw, reference, W, cy, font_ref, "#111111")
    cy += 24

    # --- 5. DASHED SEPARATOR ---
    cy = _draw_dashes(draw, cy, W, PAD)

    # --- 6. TRIP DETAILS ---
    trip_lines = [
        ("From", f"{origin_terminal}, {origin_city}"),
        ("To", f"{dest_terminal}, {dest_city}"),
        ("Date", date),
        ("Departure", time),
        ("Seat", seat_number),
    ]
    for label, value in trip_lines:
        draw.text((PAD, cy), label, fill="#888888", font=font_sm)
        draw.text((PAD, cy + 12), value, fill="#111111", font=font_reg)
        cy += 22

    # --- 7. DASHED SEPARATOR ---
    cy = _draw_dashes(draw, cy, W, PAD)

    # --- 8. PASSENGER ---
    draw.text((PAD, cy), "Passenger", fill="#888888", font=font_sm)
    draw.text((PAD, cy + 12), passenger_name, fill="#111111", font=font_bold)
    cy += 25
    if passenger_phone:
        draw.text((PAD, cy), "Phone", fill="#888888", font=font_sm)
        draw.text((PAD, cy + 12), passenger_phone, fill="#111111", font=font_reg)
        cy += 22

    # --- 9. DASHED SEPARATOR ---
    cy = _draw_dashes(draw, cy, W, PAD)

    # --- 10. PAYMENT ---
    draw.text((PAD, cy), "Amount", fill="#888888", font=font_sm)
    draw.text((PAD, cy + 12), amount_str, fill="#111111", font=font_bold)
    cy += 25
    draw.text((PAD, cy), "Status", fill="#888888", font=font_sm)
    draw.text((PAD, cy + 12), status, fill="#111111", font=font_reg)
    cy += 22
    draw.text((PAD, cy), "Method", fill="#888888", font=font_sm)
    draw.text((PAD, cy + 12), payment_method, fill="#111111", font=font_reg)
    cy += 25

    # --- 11. DASHED SEPARATOR ---
    cy = _draw_dashes(draw, cy, W, PAD)

    # --- 12. FOOTER ---
    _draw_centered(draw, "Thank you for choosing", W, cy, font_sm, "#888888")
    cy += 13
    _draw_centered(draw, "Enviable Transport!", W, cy, font_sm, "#888888")
    cy += 15
    _draw_centered(draw, "Safe travels!", W, cy, font_sm, "#aaaaaa")

    return img


def _draw_centered(draw: ImageDraw.ImageDraw, text: str, width: int, y: int, font: ImageFont.FreeTypeFont, fill: str) -> None:
    """Draw text centered horizontally."""
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    draw.text(((width - tw) // 2, y), text, fill=fill, font=font)


def _draw_dashes(draw: ImageDraw.ImageDraw, y: int, width: int, pad: int) -> int:
    """Draw a dashed separator line and return new y position."""
    y += 4
    dash_text = "- " * ((width - pad * 2) // 10)
    draw.text((pad, y), dash_text, fill="#cccccc", font=ImageFont.load_default())
    y += 12
    return y
