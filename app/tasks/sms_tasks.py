import asyncio

from app.tasks.celery_app import celery_app
from app.integrations.termii import TermiiClient


@celery_app.task(name="send_sms", bind=True, max_retries=3)
def send_sms_task(self, to: str, message: str):
    try:
        client = TermiiClient()
        return asyncio.run(client.send_sms(to, message))
    except Exception as exc:
        self.retry(exc=exc, countdown=60 * (self.request.retries + 1))


@celery_app.task(name="send_otp_sms")
def send_otp_sms(to: str, otp: str):
    message = f"Your ETBP verification code is {otp}. Valid for 10 minutes."
    send_sms_task.delay(to, message)


@celery_app.task(name="send_booking_sms")
def send_booking_sms(to: str, booking_reference: str):
    message = f"Your ETBP booking {booking_reference} is confirmed. Have a safe trip!"
    send_sms_task.delay(to, message)
