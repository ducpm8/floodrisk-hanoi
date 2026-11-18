"""Admin alert email notifications.

SMTP is used only when ``EMAIL_SMTP_HOST`` and ``ADMIN_ALERT_EMAIL`` are set.
Otherwise the alert is logged, which keeps local dev and tests dependency-free
(CLAUDE.md: email always mocked in tests).
"""

import logging
import smtplib
from email.message import EmailMessage

from api.config import settings

logger = logging.getLogger(__name__)


def send_admin_alert(subject: str, body: str) -> bool:
    """Send an admin alert. Returns True when an email was actually sent.

    Never raises: crawler alerting must not break a crawl.
    """
    recipient = settings.admin_alert_email
    if not settings.email_smtp_host or not recipient:
        logger.error("ADMIN ALERT (email not configured): %s — %s", subject, body)
        return False

    message = EmailMessage()
    message["Subject"] = f"[FloodRisk] {subject}"
    message["From"] = settings.email_smtp_user or "alerts@localhost"
    message["To"] = recipient
    message.set_content(body)

    try:
        _send_smtp(message)
        logger.error("ADMIN ALERT emailed to %s: %s", recipient, subject)
        return True
    except Exception as exc:  # pragma: no cover - network failure path
        logger.error("Failed to send admin alert email: %s", exc)
        return False


def _send_smtp(message: EmailMessage) -> None:
    with smtplib.SMTP(settings.email_smtp_host, settings.email_smtp_port) as smtp:
        smtp.starttls()
        if settings.email_smtp_user:
            smtp.login(settings.email_smtp_user, settings.email_smtp_pass)
        smtp.send_message(message)
