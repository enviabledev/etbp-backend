from app.tasks.celery_app import celery_app
from app.integrations.ses import SESClient


@celery_app.task(name="send_email", bind=True, max_retries=3)
def send_email_task(self, to: str, subject: str, html_body: str, text_body: str | None = None):
    try:
        ses = SESClient()
        return ses.send_email(to=to, subject=subject, html_body=html_body, text_body=text_body)
    except Exception as exc:
        self.retry(exc=exc, countdown=60 * (self.request.retries + 1))


@celery_app.task(name="send_booking_confirmation_email")
def send_booking_confirmation_email(to: str, booking_reference: str, passenger_name: str):
    html = f"""
    <h2>Booking Confirmed!</h2>
    <p>Dear {passenger_name},</p>
    <p>Your booking <strong>{booking_reference}</strong> has been confirmed.</p>
    <p>Thank you for choosing ETBP!</p>
    """
    send_email_task.delay(to, f"Booking {booking_reference} Confirmed", html)


@celery_app.task(name="send_password_reset_email")
def send_password_reset_email(to: str, otp: str):
    html = f"""
    <h2>Password Reset</h2>
    <p>Your password reset code is: <strong>{otp}</strong></p>
    <p>This code expires in 10 minutes.</p>
    """
    send_email_task.delay(to, "Password Reset - ETBP", html)
