import asyncio
import logging

from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="send_sms", bind=True, max_retries=3)
def send_sms_task(self, to: str, message: str):
    """Send an SMS via Termii. All SMS tasks funnel through here."""
    try:
        from app.integrations.termii import TermiiClient
        client = TermiiClient()
        result = asyncio.run(client.send_sms(to, message))
        logger.info("SMS sent to %s", to)
        return result
    except Exception as exc:
        logger.warning("SMS to %s failed (attempt %d): %s", to, self.request.retries + 1, exc)
        self.retry(exc=exc, countdown=60 * (self.request.retries + 1))


@celery_app.task(name="send_otp_sms")
def send_otp_sms(to: str, otp: str):
    message = f"Your ETBP verification code is {otp}. Valid for 10 minutes. Do not share this code."
    send_sms_task.delay(to, message)
