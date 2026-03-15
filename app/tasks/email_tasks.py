import logging

from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="send_email", bind=True, max_retries=3)
def send_email_task(self, to: str, subject: str, html_body: str, text_body: str | None = None):
    """Low-level email send via AWS SES. Other tasks should call this."""
    try:
        from app.integrations.ses import SESClient
        ses = SESClient()
        result = ses.send_email(to=to, subject=subject, html_body=html_body, text_body=text_body)
        logger.info("Email sent to %s: %s (message_id=%s)", to, subject, result.get("message_id"))
        return result
    except Exception as exc:
        logger.warning("Email to %s failed (attempt %d): %s", to, self.request.retries + 1, exc)
        self.retry(exc=exc, countdown=60 * (self.request.retries + 1))


@celery_app.task(name="send_templated_email", bind=True, max_retries=3)
def send_templated_email(self, to: str, subject: str, html_body: str):
    """Send a pre-rendered HTML email. Called by the notification service."""
    try:
        from app.integrations.ses import SESClient
        ses = SESClient()
        result = ses.send_email(to=to, subject=subject, html_body=html_body)
        logger.info("Templated email sent to %s: %s", to, subject)
        return result
    except Exception as exc:
        logger.warning("Templated email to %s failed (attempt %d): %s", to, self.request.retries + 1, exc)
        self.retry(exc=exc, countdown=60 * (self.request.retries + 1))


@celery_app.task(name="send_password_reset_email")
def send_password_reset_email(to: str, otp: str):
    from app.services.notification_service import render_template
    # Simple inline template for OTP (no dedicated template file needed)
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">
        <div style="background:#1a237e;color:white;padding:24px;text-align:center;">
            <h1 style="margin:0;font-size:22px;">Password Reset</h1>
        </div>
        <div style="padding:24px;">
            <p>Your password reset code is:</p>
            <div style="background:#f5f5f5;border-radius:8px;padding:20px;text-align:center;margin:16px 0;">
                <span style="font-size:32px;font-weight:bold;letter-spacing:8px;color:#1a237e;">{otp}</span>
            </div>
            <p>This code expires in <strong>10 minutes</strong>.</p>
            <p style="color:#999;font-size:13px;">If you didn't request this, please ignore this email.</p>
        </div>
        <div style="background:#f5f5f5;padding:16px;text-align:center;color:#999;font-size:12px;">
            Enviable Transport Booking Platform
        </div>
    </div>
    """
    send_email_task.delay(to, "Password Reset — ETBP", html)


@celery_app.task(name="send_welcome_email")
def send_welcome_email(to: str, first_name: str):
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">
        <div style="background:#1a237e;color:white;padding:24px;text-align:center;">
            <h1 style="margin:0;font-size:22px;">Welcome to ETBP!</h1>
        </div>
        <div style="padding:24px;">
            <p>Hi {first_name},</p>
            <p>Welcome to the Enviable Transport Booking Platform! We're excited to have you on board.</p>
            <p>With ETBP, you can:</p>
            <ul>
                <li>Search and book trips across Nigeria</li>
                <li>Choose your preferred seats</li>
                <li>Pay securely via card or wallet</li>
                <li>Get instant e-tickets with QR codes</li>
            </ul>
            <p>Start by searching for your next trip!</p>
            <p>Safe travels,<br>The ETBP Team</p>
        </div>
        <div style="background:#f5f5f5;padding:16px;text-align:center;color:#999;font-size:12px;">
            Enviable Transport Booking Platform
        </div>
    </div>
    """
    send_email_task.delay(to, "Welcome to ETBP!", html)
