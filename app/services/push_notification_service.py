import logging
from app.integrations.firebase import is_initialized

logger = logging.getLogger(__name__)


async def send_push(token: str, title: str, body: str, data: dict | None = None) -> bool:
    if not is_initialized():
        logger.debug("Firebase not initialized, skipping push: %s", title)
        return False

    try:
        from firebase_admin import messaging

        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in (data or {}).items()},
            token=token,
        )
        response = messaging.send(message)
        logger.info("Push sent to %s...: %s", token[:20], response)
        return True
    except Exception as e:
        err_name = type(e).__name__
        if "Unregistered" in err_name or "InvalidArgument" in err_name:
            logger.warning("Token invalid/unregistered: %s...", token[:20])
        else:
            logger.error("Push failed: %s", e)
        return False


async def send_push_to_multiple(tokens: list[str], title: str, body: str, data: dict | None = None) -> int:
    if not is_initialized() or not tokens:
        return 0

    try:
        from firebase_admin import messaging

        message = messaging.MulticastMessage(
            notification=messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in (data or {}).items()},
            tokens=tokens,
        )
        response = messaging.send_each_for_multicast(message)
        logger.info("Multicast push: %d success, %d failures", response.success_count, response.failure_count)
        return response.success_count
    except Exception as e:
        logger.error("Multicast push failed: %s", e)
        return 0


async def send_push_to_user(db, user_id, title: str, body: str, data: dict | None = None, app_type: str | None = None):
    """Send push to all active device tokens for a user."""
    from sqlalchemy import select
    from app.models.device_token import DeviceToken

    query = select(DeviceToken.token).where(DeviceToken.user_id == user_id, DeviceToken.is_active == True)  # noqa: E712
    if app_type:
        query = query.where(DeviceToken.app_type == app_type)

    result = await db.execute(query)
    tokens = [row for row in result.scalars().all()]

    if not tokens:
        return 0

    if len(tokens) == 1:
        success = await send_push(tokens[0], title, body, data)
        return 1 if success else 0

    return await send_push_to_multiple(tokens, title, body, data)
