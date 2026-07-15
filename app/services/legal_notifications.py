from __future__ import annotations

import html
import logging
import smtplib
from email.message import EmailMessage

from app.core.config import get_settings
from app.models.takedown_models import ValidatedTakedownReport

logger = logging.getLogger("apexload.legal_notifications")


def legal_email_configured() -> bool:
    settings = get_settings()
    return bool(
        settings.smtp_host
        and settings.legal_notification_email
        and settings.legal_from_email
    )


def deliver_takedown_report(
    report: ValidatedTakedownReport,
    reference_id: str,
    client_key: str,
) -> bool:
    """Deliver the internal report, then attempt a non-blocking acknowledgement."""

    if not legal_email_configured():
        logger.warning("Legal notification is unavailable for reference %s", reference_id)
        return False

    internal = _message(
        subject=f"ApexLoad legal report {reference_id} ({report.report_type})",
        recipient=get_settings().legal_notification_email,
        body=_internal_body(report, reference_id, client_key),
        reply_to=report.email,
    )
    if not _send(internal):
        logger.warning("Legal notification delivery failed for reference %s", reference_id)
        return False

    acknowledgement = _message(
        subject=f"ApexLoad legal report received: {reference_id}",
        recipient=report.email,
        body=_acknowledgement_body(reference_id),
        reply_to=get_settings().legal_notification_email,
    )
    if not _send(acknowledgement):
        logger.warning("Legal acknowledgement delivery failed for reference %s", reference_id)
    return True


def _message(*, subject: str, recipient: str, body: str, reply_to: str) -> EmailMessage:
    settings = get_settings()
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.legal_from_email
    message["To"] = recipient
    message["Reply-To"] = reply_to
    # Plain text only; _safe_display also escapes HTML-like user input.
    message.set_content(body)
    return message


def _send(message: EmailMessage) -> bool:
    settings = get_settings()
    try:
        smtp_class = smtplib.SMTP_SSL if settings.smtp_use_ssl else smtplib.SMTP
        with smtp_class(
            settings.smtp_host,
            settings.smtp_port,
            timeout=settings.legal_email_timeout_seconds,
        ) as smtp:
            if settings.smtp_use_tls and not settings.smtp_use_ssl:
                smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
        return True
    except Exception as exc:
        logger.warning("SMTP delivery failed: %s", _safe_error(exc))
        return False


def _internal_body(
    report: ValidatedTakedownReport,
    reference_id: str,
    client_key: str,
) -> str:
    return "\n".join(
        (
            "ApexLoad public legal report",
            f"Reference ID: {reference_id}",
            f"Submitted (UTC): {report.submitted_at.isoformat()}",
            f"Report type: {_safe_display(report.report_type)}",
            f"Full name: {_safe_display(report.full_name)}",
            f"Rights holder/company: {_safe_display(report.company_or_rights_holder) or '(not provided)'}",
            f"Reporter email: {_safe_display(report.email)}",
            f"Original work URL/reference: {_safe_display(report.original_work_url) or '(not provided)'}",
            f"Reported URL/reference: {_safe_display(report.reported_url_or_reference) or '(not provided)'}",
            "",
            "Explanation:",
            _safe_display(report.explanation),
            "",
            "Declarations: good faith confirmed; accuracy confirmed; authority confirmed.",
            f"Electronic signature: {_safe_display(report.electronic_signature)}",
            f"Operational request key: {client_key[:16]}",
        )
    )


def _acknowledgement_body(reference_id: str) -> str:
    return "\n".join(
        (
            "We received your ApexLoad legal report.",
            f"Reference ID: {reference_id}",
            "Receipt does not guarantee removal or legal action.",
            "Incomplete, fraudulent, or materially misleading reports may be rejected.",
            "For follow-up, contact copyright@apexload.org.",
        )
    )


def _safe_display(value: str) -> str:
    return html.escape(value, quote=False)


def _safe_error(exc: Exception) -> str:
    return " ".join(str(exc).split())[:240] or exc.__class__.__name__
