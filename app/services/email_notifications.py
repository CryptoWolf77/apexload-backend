import logging
import smtplib
from email.message import EmailMessage

from app.core.config import get_settings

logger = logging.getLogger("apexload.email")


def smtp_configured() -> bool:
    settings = get_settings()
    return bool(
        settings.smtp_host
        and settings.smtp_from_email
        and settings.admin_alert_email
    )


def send_admin_email(subject: str, body: str) -> bool:
    settings = get_settings()
    if not smtp_configured():
        logger.warning("SMTP is not configured; skipping admin email alert.")
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
    message["To"] = settings.admin_alert_email
    message.set_content(body)

    try:
        if settings.smtp_use_ssl and settings.smtp_use_tls:
            logger.warning("SMTP_USE_SSL is enabled; STARTTLS is skipped.")
        smtp_class = smtplib.SMTP_SSL if settings.smtp_use_ssl else smtplib.SMTP
        with smtp_class(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
            if settings.smtp_use_tls and not settings.smtp_use_ssl:
                smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
        logger.info("Admin email sent: %s", subject)
        return True
    except Exception as exc:
        logger.warning("Admin email failed to send: %s", _safe_error(exc))
        return False


def _safe_error(exc: Exception) -> str:
    return " ".join(str(exc).split())[:240] or exc.__class__.__name__
