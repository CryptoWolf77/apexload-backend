from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import Request

from app.core.config import Settings
from app.models.takedown_models import ValidatedTakedownReport


class TakedownRateLimitedError(Exception):
    pass


class DuplicateTakedownError(Exception):
    pass


class TakedownSubmissionGuard:
    """Process-local, short-lived abuse controls for the public form.

    This is intentionally an in-memory fallback because the current backend has
    no shared cache. Deployments with multiple instances must replace it with a
    shared store such as Redis before relying on cross-instance limits.
    """

    def __init__(self) -> None:
        self._hourly: dict[str, deque[float]] = defaultdict(deque)
        self._daily: dict[str, deque[float]] = defaultdict(deque)
        self._fingerprints: dict[str, float] = {}
        self._process_secret = secrets.token_bytes(32)
        self._lock = Lock()

    def client_key(self, client_ip: str, settings: Settings) -> str:
        return self._digest(client_ip, settings)

    def fingerprint(self, report: ValidatedTakedownReport, settings: Settings) -> str:
        value = "\x1f".join(
            (
                report.full_name.casefold(),
                report.company_or_rights_holder.casefold(),
                report.email.casefold(),
                report.report_type,
                report.original_work_url,
                report.reported_url_or_reference,
                report.explanation,
                report.electronic_signature.casefold(),
            )
        )
        return self._digest(value, settings)

    def reserve(self, client_key: str, fingerprint: str, settings: Settings) -> None:
        now = time.monotonic()
        with self._lock:
            self._discard_expired(now)
            hourly = self._hourly[client_key]
            daily = self._daily[client_key]
            self._trim(hourly, now - 3600)
            self._trim(daily, now - 86400)
            if len(hourly) >= settings.legal_rate_limit_hour or len(daily) >= settings.legal_rate_limit_day:
                raise TakedownRateLimitedError
            if fingerprint in self._fingerprints:
                raise DuplicateTakedownError
            hourly.append(now)
            daily.append(now)
            self._fingerprints[fingerprint] = now + settings.legal_pending_window_seconds

    def mark_delivered(self, fingerprint: str, settings: Settings) -> None:
        with self._lock:
            self._fingerprints[fingerprint] = time.monotonic() + settings.legal_duplicate_window_seconds

    def release(self, fingerprint: str) -> None:
        with self._lock:
            self._fingerprints.pop(fingerprint, None)

    def reset_for_tests(self) -> None:
        with self._lock:
            self._hourly.clear()
            self._daily.clear()
            self._fingerprints.clear()

    def _digest(self, value: str, settings: Settings) -> str:
        secret = settings.legal_fingerprint_secret.encode() if settings.legal_fingerprint_secret else self._process_secret
        return hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()

    def _discard_expired(self, now: float) -> None:
        for fingerprint, expiry in tuple(self._fingerprints.items()):
            if expiry <= now:
                self._fingerprints.pop(fingerprint, None)

    @staticmethod
    def _trim(bucket: deque[float], cutoff: float) -> None:
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()


def client_ip_for_takedown(request: Request, settings: Settings) -> str:
    direct_ip = request.client.host if request.client and request.client.host else "unknown"
    if not _is_trusted_proxy(direct_ip, settings.legal_trusted_proxy_cidrs):
        return direct_ip
    cloudflare_ip = request.headers.get("cf-connecting-ip", "")
    if _is_ip(cloudflare_ip):
        return cloudflare_ip
    return direct_ip


def _is_trusted_proxy(client_ip: str, cidrs: list[str]) -> bool:
    if not cidrs or not _is_ip(client_ip):
        return False
    address = ipaddress.ip_address(client_ip)
    for cidr in cidrs:
        try:
            if address in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


takedown_submission_guard = TakedownSubmissionGuard()
