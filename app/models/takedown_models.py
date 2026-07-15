from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parseaddr

from pydantic import BaseModel, ConfigDict, StrictBool

from app.core.config import Settings

_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_REPORT_TYPES = {"copyright", "privacy", "impersonation", "other"}


class TakedownValidationError(ValueError):
    """A public validation failure that must not expose submitted content."""


class TakedownRequest(BaseModel):
    """Accept the public contract and the website's existing field aliases."""

    model_config = ConfigDict(extra="forbid")

    fullName: str
    companyOrRightsHolder: str | None = None
    companyName: str | None = None
    email: str
    reportType: str
    originalWorkUrl: str | None = None
    originalWorkReference: str | None = None
    reportedUrlOrReference: str | None = None
    reportedReference: str | None = None
    explanation: str
    goodFaithConfirmed: StrictBool | None = None
    goodFaithAccepted: StrictBool | None = None
    accuracyConfirmed: StrictBool | None = None
    authorityConfirmed: StrictBool | None = None
    accuracyAuthorityAccepted: StrictBool | None = None
    electronicSignature: str
    website: str = ""
    formStartedAt: str
    contactConsent: StrictBool | None = None
    language: str | None = None


@dataclass(frozen=True)
class ValidatedTakedownReport:
    full_name: str
    company_or_rights_holder: str
    email: str
    report_type: str
    original_work_url: str
    reported_url_or_reference: str
    explanation: str
    electronic_signature: str
    submitted_at: datetime


def validate_takedown_request(
    payload: TakedownRequest,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> ValidatedTakedownReport:
    full_name = _text(payload.fullName, "full name", 2, 120, required=True)
    company = _text(
        payload.companyOrRightsHolder or payload.companyName or "",
        "company",
        0,
        160,
        required=False,
    )
    email = _email(payload.email)
    report_type = _text(payload.reportType, "report type", 1, 32, required=True).lower()
    if report_type not in _REPORT_TYPES:
        raise TakedownValidationError("Unsupported report type.")

    original_work = _text(
        payload.originalWorkUrl or payload.originalWorkReference or "",
        "original work reference",
        3,
        2000,
        required=False,
    )
    reported_reference = _text(
        payload.reportedUrlOrReference or payload.reportedReference or "",
        "reported reference",
        3,
        2000,
        required=False,
    )
    if not original_work and not reported_reference:
        raise TakedownValidationError("Provide a useful identifying reference.")

    explanation = _text(payload.explanation, "explanation", 40, 5000, required=True)
    signature = _text(
        payload.electronicSignature,
        "electronic signature",
        2,
        120,
        required=True,
    )
    website = _text(payload.website, "website", 0, 256, required=False)
    if website:
        raise TakedownValidationError("The report could not be accepted.")

    good_faith = _declaration(payload.goodFaithConfirmed, payload.goodFaithAccepted)
    accuracy = _declaration(payload.accuracyConfirmed, payload.accuracyAuthorityAccepted)
    authority = _declaration(payload.authorityConfirmed, payload.accuracyAuthorityAccepted)
    if not all((good_faith, accuracy, authority)):
        raise TakedownValidationError("Required declarations were not confirmed.")

    submitted_at = _started_at(payload.formStartedAt, settings, now=now)
    return ValidatedTakedownReport(
        full_name=full_name,
        company_or_rights_holder=company,
        email=email,
        report_type=report_type,
        original_work_url=original_work,
        reported_url_or_reference=reported_reference,
        explanation=explanation,
        electronic_signature=signature,
        submitted_at=submitted_at,
    )


def _declaration(primary: bool | None, legacy: bool | None) -> bool:
    return primary is True or legacy is True


def _text(value: str, field: str, minimum: int, maximum: int, *, required: bool) -> str:
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise TakedownValidationError(f"Invalid {field}.")
    normalized = " ".join(value.strip().split())
    if required and not normalized:
        raise TakedownValidationError(f"Missing {field}.")
    if normalized and not minimum <= len(normalized) <= maximum:
        raise TakedownValidationError(f"Invalid {field}.")
    return normalized


def _email(value: str) -> str:
    email = _text(value, "email", 3, 254, required=True)
    display_name, parsed = parseaddr(email)
    if display_name or parsed != email or not _EMAIL_PATTERN.fullmatch(email):
        raise TakedownValidationError("Invalid email.")
    return email


def _started_at(value: str, settings: Settings, *, now: datetime | None) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise TakedownValidationError("Invalid form timestamp.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    submitted_at = parsed.astimezone(UTC)
    current = now or datetime.now(UTC)
    elapsed = (current - submitted_at).total_seconds()
    if elapsed < -settings.legal_clock_skew_seconds:
        raise TakedownValidationError("Please refresh the form and try again.")
    if 0 <= elapsed < settings.legal_min_form_seconds:
        raise TakedownValidationError("Please take a moment to review the form.")
    return submitted_at
