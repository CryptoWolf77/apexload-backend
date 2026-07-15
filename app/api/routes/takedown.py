from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from app.core.config import get_settings
from app.core.takedown_protection import (
    DuplicateTakedownError,
    TakedownRateLimitedError,
    client_ip_for_takedown,
    takedown_submission_guard,
)
from app.models.takedown_models import (
    TakedownRequest,
    TakedownValidationError,
    validate_takedown_request,
)
from app.services.legal_notifications import deliver_takedown_report

router = APIRouter(tags=["public-legal"])
logger = logging.getLogger("apexload.takedown")


@router.post("/v1/public/takedown", status_code=status.HTTP_201_CREATED)
async def submit_takedown(request: Request) -> JSONResponse:
    settings = get_settings()
    _validate_origin(request, settings.legal_allowed_origins)
    _validate_content_length(request, settings.legal_max_request_bytes)
    payload = await _read_payload(request, settings.legal_max_request_bytes)
    try:
        candidate = TakedownRequest.model_validate(payload)
        report = validate_takedown_request(candidate, settings)
    except (ValidationError, TakedownValidationError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Please review the report fields and try again.",
        ) from None

    client_ip = client_ip_for_takedown(request, settings)
    client_key = takedown_submission_guard.client_key(client_ip, settings)
    fingerprint = takedown_submission_guard.fingerprint(report, settings)
    try:
        takedown_submission_guard.reserve(client_key, fingerprint, settings)
    except TakedownRateLimitedError:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many reports were submitted. Please try again later.",
            headers={"Retry-After": "3600"},
        ) from None
    except DuplicateTakedownError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An identical report is already being processed.",
        ) from None

    reference_id = _reference_id()
    try:
        delivered = await asyncio.wait_for(
            run_in_threadpool(deliver_takedown_report, report, reference_id, client_key),
            timeout=settings.legal_email_timeout_seconds,
        )
    except TimeoutError:
        delivered = False
    except Exception:
        logger.warning("Legal delivery failed for reference %s", reference_id)
        delivered = False

    if not delivered:
        takedown_submission_guard.release(fingerprint)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The report could not be delivered. Please try again later or email copyright@apexload.org.",
        )

    takedown_submission_guard.mark_delivered(fingerprint, settings)
    logger.info(
        "Legal report accepted reference=%s type=%s client=%s",
        reference_id,
        report.report_type,
        client_key[:16],
    )
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        headers={"Cache-Control": "no-store"},
        content={
            "success": True,
            "referenceId": reference_id,
            # Retained during website rollout; the current form expects `reference`.
            "reference": reference_id,
            "message": "Your report was submitted successfully.",
        },
    )


def _validate_origin(request: Request, allowed_origins: list[str]) -> None:
    origin = request.headers.get("origin")
    if origin and origin not in allowed_origins:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Request origin is not allowed.")


def _validate_content_length(request: Request, maximum: int) -> None:
    value = request.headers.get("content-length")
    if not value:
        return
    try:
        if int(value) > maximum:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Request is too large.")
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid request size.") from None


async def _read_payload(request: Request, maximum: int) -> dict[str, object]:
    content_type = request.headers.get("content-type", "")
    if not content_type.lower().startswith("application/json"):
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="JSON is required.")
    body = await request.body()
    if len(body) > maximum:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Request is too large.")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON request.") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON request.")
    return payload


def _reference_id() -> str:
    return f"APL-TD-{datetime.now(UTC):%Y%m%d}-{uuid4().hex[:10].upper()}"
