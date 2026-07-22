import json
import logging
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.services.email_notifications import send_admin_email
from app.services.instagram_auth_service import instagram_cookie_path
from app.services.instagram_cookie_health import check_instagram_cookies
from app.services.instagram_error_classifier import (
    INSTAGRAM_TEMPORARILY_BUSY,
    INSTAGRAM_TEMPORARILY_UNAVAILABLE,
    InstagramErrorClassification,
    classify_instagram_error,
)

logger = logging.getLogger("apexload.instagram_safety")

SAFETY_LIMITED_CODE = "INSTAGRAM_TEMPORARILY_LIMITED"
SAFETY_UNAVAILABLE_CODE = "INSTAGRAM_TEMPORARILY_UNAVAILABLE"
REAL_REQUEST_SOURCE = "real_user_request"
HEALTH_CHECK_SOURCE = "health_check"
COOKIE_OR_HEALTH_STATE_CATEGORIES = {
    "cookies_missing",
    "cookies_empty",
    "cookies_expired",
    "cookies_invalid",
    "cookies_invalid_real_request",
    "cookies_warning",
    "health_warning",
}
REAL_REQUEST_HARD_CATEGORIES = {
    "instagram_restricted",
    "instagram_challenge_required",
    "instagram_login_required",
    "instagram_rate_limited",
    "cookies_missing",
    "cookies_empty",
    "cookies_expired",
    "cookies_invalid",
}


class InstagramSafetyDecision:
    def __init__(
        self,
        allowed: bool,
        code: str | None = None,
        message: str = "",
        acquired: bool = False,
    ) -> None:
        self.allowed = allowed
        self.code = code
        self.message = message
        self.acquired = acquired


class InstagramSafetyService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._minute_requests: deque[float] = deque()
        self._hour_requests: deque[float] = deque()
        self._semaphore = threading.Semaphore(max(get_settings().instagram_max_concurrent_jobs, 1))
        self._state = self._load_state()

    def begin_request(self) -> InstagramSafetyDecision:
        settings = get_settings()
        if not settings.instagram_safety_mode_enabled:
            return InstagramSafetyDecision(True)
        with self._lock:
            self._refresh_daily_totals_locked()
            paused = self._paused_decision_locked()
            if paused:
                return paused
            limited = self._rate_limit_decision_locked()
            if limited:
                return limited
        acquired = self._semaphore.acquire(blocking=False)
        if not acquired:
            return InstagramSafetyDecision(
                False,
                SAFETY_LIMITED_CODE,
                INSTAGRAM_TEMPORARILY_BUSY,
            )
        return InstagramSafetyDecision(True, acquired=True)

    def finish_success(self, decision: InstagramSafetyDecision | None = None) -> None:
        if decision and decision.acquired:
            self._semaphore.release()
        settings = get_settings()
        if not settings.instagram_safety_mode_enabled:
            return
        with self._lock:
            previous_mode = str(self._state.get("mode") or "active")
            previous_category = str(self._state.get("reason_category") or "")
            self._refresh_daily_totals_locked()
            now = _now()
            self._state["last_success_at"] = now
            self._state["consecutive_successes"] = int(self._state.get("consecutive_successes") or 0) + 1
            self._state["consecutive_failures"] = 0
            self._state["total_successes_today"] = int(self._state.get("total_successes_today") or 0) + 1
            self._clear_health_warning_locked()
            if previous_mode == "paused" and previous_category in COOKIE_OR_HEALTH_STATE_CATEGORIES:
                self._state.update(
                    mode="active",
                    reason="Instagram recovered after a successful real request.",
                    reason_category="",
                    paused_until=None,
                )
                logger.info("Instagram Safety Mode cleared stale cookie/health pause after success.")
                self._send_recovery_email_locked()
            elif previous_mode == "paused":
                logger.info(
                    "Instagram request succeeded while a hard Safety Mode pause is active; "
                    "keeping pause category=%s.",
                    previous_category,
                )
            elif previous_mode in {"limited", "degraded"}:
                self._state.update(
                    mode="active",
                    reason="Instagram recovered successfully.",
                    reason_category="",
                    paused_until=None,
                )
                self._send_recovery_email_locked()
            else:
                self._state.update(mode="active", reason="", reason_category="", paused_until=None)
            self._touch_and_save_locked()

    def finish_neutral(self, decision: InstagramSafetyDecision | None = None) -> None:
        if decision and decision.acquired:
            self._semaphore.release()

    def finish_failure(
        self,
        error: object,
        decision: InstagramSafetyDecision | None = None,
        source: str = REAL_REQUEST_SOURCE,
    ) -> InstagramErrorClassification:
        if decision and decision.acquired:
            self._semaphore.release()
        classification = classify_instagram_error(error)
        settings = get_settings()
        if not settings.instagram_safety_mode_enabled:
            return classification
        with self._lock:
            self._refresh_daily_totals_locked()
            now = _now()
            failures = int(self._state.get("consecutive_failures") or 0) + 1
            self._state["last_error_at"] = now
            self._state["consecutive_failures"] = failures
            self._state["consecutive_successes"] = 0
            self._state["total_failures_today"] = int(self._state.get("total_failures_today") or 0) + 1
            state_category = _state_category_for(classification, source)
            cooldown = self._cooldown_for(classification, failures)
            if cooldown is not None:
                until = datetime.now(timezone.utc) + cooldown
                self._state.update(
                    mode="paused",
                    reason=classification.safe_user_message,
                    reason_category=state_category,
                    paused_until=until.isoformat(),
                    last_trigger_source=source,
                )
                self._send_alert_email_locked(classification, source, state_category)
            else:
                self._state.update(
                    mode="limited",
                    reason=classification.safe_user_message,
                    reason_category=state_category,
                    paused_until=None,
                    last_trigger_source=source,
                )
            self._touch_and_save_locked()
        return classification

    def status(self) -> dict[str, Any]:
        with self._lock:
            state = dict(self._state)
            state["config"] = self.safe_config()
            return state

    def safe_config(self) -> dict[str, Any]:
        settings = get_settings()
        return {
            "enabled": settings.instagram_safety_mode_enabled,
            "maxConcurrentJobs": settings.instagram_max_concurrent_jobs,
            "maxRequestsPerMinute": settings.instagram_max_requests_per_minute,
            "maxRequestsPerHour": settings.instagram_max_requests_per_hour,
            "failureThreshold": settings.instagram_failure_threshold,
            "restrictionCooldownHours": settings.instagram_restriction_cooldown_hours,
            "rateLimitCooldownHours": settings.instagram_rate_limit_cooldown_hours,
            "unknownErrorCooldownMinutes": settings.instagram_unknown_error_cooldown_minutes,
            "recoverySuccessThreshold": settings.instagram_recovery_success_threshold,
        }

    def manual_check(self) -> dict[str, Any]:
        with self._lock:
            paused = self._paused_decision_locked()
            if paused:
                return {"success": False, **self.status()}
        result = check_instagram_cookies()
        if result.get("is_valid"):
            self.record_health_success(result)
        else:
            self.record_health_warning(result)
        return {"success": bool(result.get("is_valid")), "health": result, **self.status()}

    def record_health_success(self, result: dict[str, Any] | None = None) -> None:
        settings = get_settings()
        if not settings.instagram_safety_mode_enabled:
            return
        with self._lock:
            self._state["last_health_check_at"] = _now()
            self._clear_health_warning_locked()
            if str(self._state.get("mode")) == "degraded":
                self._state.update(mode="active", reason="", reason_category="", paused_until=None)
            self._touch_and_save_locked()

    def record_health_warning(self, result: dict[str, Any]) -> None:
        settings = get_settings()
        if not settings.instagram_safety_mode_enabled:
            return
        technical_reason = result.get("technical_reason") or result.get("message") or result
        classification = classify_instagram_error(technical_reason)
        category = "cookies_warning" if classification.is_cookie_problem else "health_warning"
        with self._lock:
            self._state.update(
                mode="degraded" if str(self._state.get("mode")) == "active" else self._state.get("mode"),
                health_status="warning",
                health_warning_at=_now(),
                health_reason=classification.safe_user_message,
                health_reason_category=category,
                health_technical_reason=classification.technical_reason,
                last_health_check_at=_now(),
                last_trigger_source=HEALTH_CHECK_SOURCE,
            )
            logger.warning(
                "Instagram cookie health warning recorded without global pause: category=%s",
                category,
            )
            self._touch_and_save_locked()

    def manual_resume(self) -> dict[str, Any]:
        return self.manual_clear(reason="Manual resume by admin.", reason_category="manual_resume")

    def manual_clear(
        self,
        reason: str = "Manual Safety Mode clear by admin.",
        reason_category: str = "manual_clear",
    ) -> dict[str, Any]:
        with self._lock:
            self._state.update(
                mode="active",
                reason=reason,
                reason_category=reason_category,
                paused_until=None,
                consecutive_failures=0,
                consecutive_successes=0,
            )
            logger.info("Instagram Safety Mode cleared by admin.")
            self._touch_and_save_locked()
            return self.status()

    def _paused_decision_locked(self) -> InstagramSafetyDecision | None:
        paused_until = _parse_dt(self._state.get("paused_until"))
        if str(self._state.get("mode")) == "paused" and paused_until:
            if paused_until > datetime.now(timezone.utc):
                return InstagramSafetyDecision(
                    False,
                    SAFETY_UNAVAILABLE_CODE,
                    INSTAGRAM_TEMPORARILY_UNAVAILABLE,
                )
            self._state.update(
                mode="limited",
                reason="Cooldown expired. Allowing limited recovery attempt.",
                reason_category="cooldown_expired",
                paused_until=None,
            )
            self._touch_and_save_locked()
        return None

    def _rate_limit_decision_locked(self) -> InstagramSafetyDecision | None:
        settings = get_settings()
        now = time.monotonic()
        _trim(self._minute_requests, now - 60)
        _trim(self._hour_requests, now - 3600)
        if len(self._minute_requests) >= max(settings.instagram_max_requests_per_minute, 1):
            self._state.update(
                mode="limited",
                reason=INSTAGRAM_TEMPORARILY_BUSY,
                reason_category="instagram_rate_limited",
            )
            self._touch_and_save_locked()
            return InstagramSafetyDecision(False, SAFETY_LIMITED_CODE, INSTAGRAM_TEMPORARILY_BUSY)
        if len(self._hour_requests) >= max(settings.instagram_max_requests_per_hour, 1):
            self._state.update(
                mode="limited",
                reason=INSTAGRAM_TEMPORARILY_BUSY,
                reason_category="instagram_rate_limited",
            )
            self._touch_and_save_locked()
            return InstagramSafetyDecision(False, SAFETY_LIMITED_CODE, INSTAGRAM_TEMPORARILY_BUSY)
        self._minute_requests.append(now)
        self._hour_requests.append(now)
        return None

    def _cooldown_for(
        self,
        classification: InstagramErrorClassification,
        failures: int,
    ) -> timedelta | None:
        settings = get_settings()
        threshold = max(settings.instagram_failure_threshold, 1)
        if (
            classification.category in REAL_REQUEST_HARD_CATEGORIES
            and classification.category != "instagram_rate_limited"
            and failures >= threshold
        ):
            return timedelta(hours=max(settings.instagram_restriction_cooldown_hours, 1))
        if classification.category == "instagram_rate_limited" and failures >= threshold:
            return timedelta(hours=max(settings.instagram_rate_limit_cooldown_hours, 1))
        if classification.category == "unknown_instagram_error" and failures >= threshold:
            return timedelta(minutes=max(settings.instagram_unknown_error_cooldown_minutes, 1))
        return None

    def _send_alert_email_locked(
        self,
        classification: InstagramErrorClassification,
        trigger_source: str,
        state_category: str,
    ) -> None:
        settings = get_settings()
        now = datetime.now(timezone.utc)
        last = _parse_dt(self._state.get("last_admin_alert_at"))
        cooldown = timedelta(hours=max(settings.instagram_cookie_alert_cooldown_hours, 1))
        if last and now - last < cooldown:
            return
        body = f"""Instagram Safety Mode activated.

Safety mode: {self._state.get('mode')}
Trigger source: {trigger_source}
Reason category: {state_category}
Paused until: {self._state.get('paused_until')}
Last error time: {self._state.get('last_error_at')}
Consecutive failures: {self._state.get('consecutive_failures')}
Reason: {classification.technical_reason}

Suggested action:
{_suggested_action(state_category)}

If you confirm Instagram works outside Safety Mode, an admin can clear the pause
without deleting cookies:
POST /api/admin/instagram/safety/clear
"""
        subject = (
            "ApexLoad Alert: Instagram rate limit detected"
            if classification.is_rate_limit
            else "ApexLoad Alert: Instagram Safety Mode activated"
        )
        if send_admin_email(subject, body):
            self._state["last_admin_alert_at"] = now.isoformat()

    def _send_recovery_email_locked(self) -> None:
        now = datetime.now(timezone.utc)
        last = _parse_dt(self._state.get("last_recovery_alert_at"))
        if last and self._state.get("last_success_at") and last.isoformat() >= str(self._state["last_success_at"]):
            return
        body = f"""Instagram downloads are active again.

Safety mode: active
Last success time: {self._state.get('last_success_at')}
Consecutive successes: {self._state.get('consecutive_successes')}
"""
        if send_admin_email("ApexLoad Recovery: Instagram downloads are active again", body):
            self._state["last_recovery_alert_at"] = now.isoformat()

    def _refresh_daily_totals_locked(self) -> None:
        today = datetime.now(timezone.utc).date().isoformat()
        if self._state.get("counter_date") != today:
            self._state["counter_date"] = today
            self._state["total_failures_today"] = 0
            self._state["total_successes_today"] = 0

    def _clear_health_warning_locked(self) -> None:
        self._state.update(
            health_status="healthy",
            health_reason="",
            health_reason_category="",
            health_technical_reason="",
            health_warning_at=None,
        )

    def _load_state(self) -> dict[str, Any]:
        path = safety_state_path()
        if path.is_file():
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    return {**_default_state(), **value}
            except (OSError, json.JSONDecodeError):
                logger.warning("Instagram safety state could not be read; resetting.")
        state = _default_state()
        _write_state(path, state)
        return state

    def _touch_and_save_locked(self) -> None:
        self._state["updated_at"] = _now()
        _write_state(safety_state_path(), self._state)


def safety_state_path() -> Path:
    settings = get_settings()
    if settings.instagram_safety_state_path:
        return Path(settings.instagram_safety_state_path).expanduser().resolve()
    return instagram_cookie_path().with_name("instagram_safety_state.json")


def _default_state() -> dict[str, Any]:
    now = _now()
    return {
        "mode": "active",
        "reason": "",
        "reason_category": "",
        "paused_until": None,
        "last_error_at": None,
        "last_success_at": None,
        "consecutive_failures": 0,
        "consecutive_successes": 0,
        "last_admin_alert_at": None,
        "last_recovery_alert_at": None,
        "last_trigger_source": "",
        "health_status": "healthy",
        "health_reason": "",
        "health_reason_category": "",
        "health_technical_reason": "",
        "health_warning_at": None,
        "last_health_check_at": None,
        "total_failures_today": 0,
        "total_successes_today": 0,
        "updated_at": now,
        "counter_date": datetime.now(timezone.utc).date().isoformat(),
    }


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _trim(bucket: deque[float], cutoff: float) -> None:
    while bucket and bucket[0] <= cutoff:
        bucket.popleft()


def _parse_dt(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_category_for(
    classification: InstagramErrorClassification,
    source: str,
) -> str:
    if source == HEALTH_CHECK_SOURCE:
        return "cookies_warning" if classification.is_cookie_problem else "health_warning"
    if classification.is_cookie_problem:
        return "cookies_invalid_real_request"
    if classification.category == "instagram_rate_limited":
        return "rate_limited"
    if classification.category in {
        "instagram_restricted",
        "instagram_challenge_required",
    }:
        return "restricted"
    if classification.category == "instagram_login_required":
        return "login_required"
    if classification.category == "unknown_instagram_error":
        return "unknown_error"
    return classification.category


def _suggested_action(category: str) -> str:
    if category in {"cookies_warning", "health_warning"}:
        return "Health check warning only. Test a real public Reel before clearing or rotating cookies."
    if category.startswith("cookies_"):
        return "Upload fresh Instagram cookies from the admin panel."
    if category in {"restricted", "rate_limited", "login_required"}:
        return (
            "Do not upload cookies repeatedly. Open the Instagram account manually, "
            "clear any warning if needed, and wait for cooldown."
        )
    return "Check backend logs and run manual health check."


instagram_safety_service = InstagramSafetyService()
