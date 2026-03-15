import io
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import qrcode
from PIL import Image, ImageDraw, ImageFont

from app.core.exceptions import ForbiddenError, NotFoundError
from app.models.booking import Booking
from app.models.route import Route
from app.models.schedule import Trip


async def generate_eticket_pdf(
    db: AsyncSession, reference: str, user_id: uuid.UUID
) -> bytes:
    result = await db.execute(
        select(Booking)
        .options(
            selectinload(Booking.passengers),
            selectinload(Booking.trip).selectinload(Trip.route).selectinload(Route.origin_terminal),
            selectinload(Booking.trip).selectinload(Trip.route).selectinload(Route.destination_terminal),
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

    # Build one ticket image per passenger, then combine into PDF pages
    pages: list[Image.Image] = []

    for passenger in booking.passengers:
        page = _render_ticket_page(
            reference=booking.reference,
            passenger_name=f"{passenger.first_name} {passenger.last_name}",
            seat_number=passenger.qr_code_data.split("-")[1] if passenger.qr_code_data and "-" in passenger.qr_code_data else "N/A",
            origin=f"{route.origin_terminal.name}, {route.origin_terminal.city}",
            destination=f"{route.destination_terminal.name}, {route.destination_terminal.city}",
            date=trip.departure_date.strftime("%d %b %Y"),
            time=trip.departure_time.strftime("%H:%M"),
            qr_data=passenger.qr_code_data or booking.reference,
            amount=f"{booking.currency} {float(booking.total_amount):,.2f}",
            status=booking.status.upper() if isinstance(booking.status, str) else booking.status,
        )
        pages.append(page)

    # Convert to PDF bytes
    pdf_buffer = io.BytesIO()
    if len(pages) == 1:
        pages[0].save(pdf_buffer, "PDF", resolution=150)
    else:
        pages[0].save(
            pdf_buffer, "PDF", resolution=150, save_all=True, append_images=pages[1:]
        )
    pdf_buffer.seek(0)
    return pdf_buffer.read()


def _render_ticket_page(
    reference: str,
    passenger_name: str,
    seat_number: str,
    origin: str,
    destination: str,
    date: str,
    time: str,
    qr_data: str,
    amount: str,
    status: str,
) -> Image.Image:
    """Render a single e-ticket page as a PIL Image."""
    width, height = 800, 400
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)

    # Use default font (no external font files needed)
    try:
        title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
        body_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
        small_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
    except (OSError, IOError):
        title_font = ImageFont.load_default()
        body_font = title_font
        small_font = title_font

    # Header
    draw.rectangle([(0, 0), (width, 55)], fill="#1a237e")
    draw.text((20, 12), "ETBP E-TICKET", fill="white", font=title_font)
    draw.text((width - 180, 16), reference, fill="white", font=body_font)

    # Body
    y = 75
    lines = [
        ("Passenger", passenger_name),
        ("From", origin),
        ("To", destination),
        ("Date", date),
        ("Departure", time),
        ("Seat", seat_number),
        ("Amount", amount),
        ("Status", status),
    ]
    for label, value in lines:
        draw.text((30, y), f"{label}:", fill="#666666", font=small_font)
        draw.text((160, y), str(value), fill="#111111", font=body_font)
        y += 32

    # QR code
    qr = qrcode.make(qr_data, box_size=4, border=2)
    qr_img = qr.resize((150, 150))
    img.paste(qr_img, (width - 190, 80))

    # Footer line
    draw.line([(20, height - 40), (width - 20, height - 40)], fill="#cccccc")
    draw.text(
        (20, height - 32),
        "Enviable Transport Booking Platform — Have a safe trip!",
        fill="#999999",
        font=small_font,
    )

    return img
