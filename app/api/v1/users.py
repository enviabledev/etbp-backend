from fastapi import APIRouter

from app.dependencies import CurrentUser, DBSession

router = APIRouter(prefix="/users", tags=["Users"])


@router.get("/me/bookings")
async def get_my_bookings(db: DBSession, current_user: CurrentUser):
    # TODO: implement
    return []


@router.get("/me/wallet")
async def get_my_wallet(db: DBSession, current_user: CurrentUser):
    # TODO: implement
    return {}


@router.get("/me/notifications")
async def get_my_notifications(db: DBSession, current_user: CurrentUser):
    # TODO: implement
    return []
