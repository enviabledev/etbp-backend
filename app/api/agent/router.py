from fastapi import APIRouter

from app.api.agent import bookings, reports

router = APIRouter(prefix="/agent")

router.include_router(bookings.router)
router.include_router(reports.router)
