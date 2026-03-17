from fastapi import APIRouter

from app.api.v1 import auth, bookings, otp, payments, reviews, routes, schedules, seats, support, terminals, users

router = APIRouter(prefix="/v1")

router.include_router(auth.router)
router.include_router(otp.router)
router.include_router(users.router)
router.include_router(terminals.router)
router.include_router(routes.router)
router.include_router(schedules.router)
router.include_router(seats.router)
router.include_router(bookings.router)
router.include_router(payments.router)
router.include_router(reviews.router)
router.include_router(support.router)
