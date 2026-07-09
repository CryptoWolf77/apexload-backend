import asyncio
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.services.email_notifications import send_admin_email, smtp_configured
from app.services.instagram_auth_service import (
    ensure_instagram_cookie_storage,
    instagram_cookie_path,
    validate_instagram_cookie_file,
)

logger = logging.getLogger("apexload.instagram_cookie_health")

INVALID_STATUSES = {"missing", "empty", "invalid", "expired", "unknown_error"}
HEALTH_JSON_NAME = "instagram_cookie_health.json"
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
FRIENDLY_INSTAGRAM_UNAVAILABLE = (
    "Instagram downloads are temporarily unavailable while we refresh the "
    "server session. Please try again later."
)

_latest_result: dict[str, Any] | None = None
_last_alert_at: datetime | None = None
_last_recovery_at: datetime | None = None
_previous_status: str | None = None
_scheduler_task: asyncio.Task | None = None


def health_json_path() -> Path:
    return instagram_cookie_path().parent / HEALTH_JSON_NAME


def initialize_instagram_cookie_storage() -> None:
    ensure_instagram_cookie_storage()


def latest_health_result() -> dict[str, Any]:
    global _latest_result
    if _latest_result is not None:
        return dict(_latest_result)
    persisted = _read_persisted_health()
    if persisted:
        _latest_result = persisted
        return dict(persisted)
    return check_instagram_cookies(send_notifications=False)


def check_instagram_cookies(
    *,
    cookie_path: Path | None = None,
    send_notifications: bool = True,
    persist: bool = True,
) -> dict[str, Any]:
    path = cookie_path or instagram_cookie_path()
    result = _basic_result(path)
    valid_file, reason = validate_instagram_cookie_file(path)

    if not path.exists():
        result.update(status="missing", message="Instagram cookie file is missing.")
    elif result["cookie_file_size"] <= 0:
        result.update(status="empty", message="Instagram cookie file is empty.")
    elif not valid_file:
        result.update(status="invalid", message=reason)
    else:
        settings = get_settings()
        if not settings.instagram_healthcheck_url:
            result.update(
                status="not_configured",
                is_valid=True,
                message=(
                    "Cookie file looks valid. Full Instagram validation is not "
                    "configured because INSTAGRAM_HEALTHCHECK_URL is empty."
                ),
                technical_reason="INSTAGRAM_HEALTHCHECK_URL is not configured.",
            )
        else:
            result.update(_run_ytdlp_cookie_validation(path, settings.instagram_healthcheck_url))

    result["is_valid"] = result["status"] in {"valid", "not_configured"}
    _stamp_success_failure(result)
    if persist:
        _set_latest_result(result)
    if send_notifications:
        _maybe_send_notification(result)
    return dict(result)


def validate_uploaded_cookie_file(temp_path: Path) -> dict[str, Any]:
    size = temp_path.stat().st_size if temp_path.exists() else 0
    if size > MAX_UPLOAD_BYTES:
        return _upload_error("invalid", "Cookie file is too large.")
    return check_instagram_cookies(
        cookie_path=temp_path,
        send_notifications=False,
        persist=False,
    )


def safe_config() -> dict[str, Any]:
    settings = get_settings()
    cookie_path = instagram_cookie_path()
    return {
        "cookiesPath": str(cookie_path),
        "checkIntervalMinutes": settings.instagram_cookie_check_interval_minutes,
        "alertCooldownHours": settings.instagram_cookie_alert_cooldown_hours,
        "alertEmailConfigured": bool(settings.admin_alert_email),
        "smtpConfigured": smtp_configured(),
        "smtpUseSsl": settings.smtp_use_ssl,
        "smtpUseTls": settings.smtp_use_tls and not settings.smtp_use_ssl,
        "smtpSecurity": "implicit_ssl"
        if settings.smtp_use_ssl
        else ("starttls" if settings.smtp_use_tls else "plain"),
        "healthcheckUrlConfigured": bool(settings.instagram_healthcheck_url),
        "adminPanelUrlConfigured": bool(settings.admin_panel_url),
    }


def start_instagram_cookie_health_scheduler() -> None:
    global _scheduler_task
    settings = get_settings()
    initialize_instagram_cookie_storage()
    if not settings.instagram_cookie_health_enabled:
        logger.info("Instagram cookie health scheduler disabled.")
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.info("No running event loop; scheduler not started.")
        return
    if _scheduler_task and not _scheduler_task.done():
        return
    _scheduler_task = loop.create_task(_scheduler_loop())


async def stop_instagram_cookie_health_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task is None:
        return
    _scheduler_task.cancel()
    try:
        await _scheduler_task
    except asyncio.CancelledError:
        pass
    _scheduler_task = None


async def _scheduler_loop() -> None:
    settings = get_settings()
    await asyncio.to_thread(check_instagram_cookies)
    interval = max(settings.instagram_cookie_check_interval_minutes, 1) * 60
    while True:
        await asyncio.sleep(interval)
        await asyncio.to_thread(check_instagram_cookies)


def _run_ytdlp_cookie_validation(path: Path, url: str) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--cookies",
        str(path),
        "--skip-download",
        "--simulate",
        "--no-warnings",
        "--dump-json",
        url,
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "unknown_error",
            "message": "Instagram cookie validation timed out.",
            "technical_reason": "yt-dlp validation timed out.",
        }
    output = f"{result.stderr}\n{result.stdout}".lower()
    if result.returncode == 0 and (result.stdout or "").strip():
        return {
            "status": "valid",
            "message": "Instagram cookies are valid.",
            "technical_reason": "yt-dlp metadata validation succeeded.",
        }
    status = "expired" if _looks_like_expired(output) else "invalid"
    return {
        "status": status,
        "message": "Instagram cookies failed validation.",
        "technical_reason": _safe_text(result.stderr or result.stdout),
    }


def _basic_result(path: Path) -> dict[str, Any]:
    exists = path.is_file()
    stat = path.stat() if exists else None
    return {
        "status": "unknown_error",
        "is_valid": False,
        "last_checked_at": _now_iso(),
        "message": "Instagram cookie health has not been checked.",
        "technical_reason": "",
        "cookie_file_exists": exists,
        "cookie_file_size": stat.st_size if stat else 0,
        "cookie_file_mtime": _iso_from_timestamp(stat.st_mtime) if stat else None,
        "last_success_at": None,
        "last_failure_at": None,
    }


def _upload_error(status: str, message: str) -> dict[str, Any]:
    result = _basic_result(Path(""))
    result.update(status=status, message=message, technical_reason=message)
    return result


def _stamp_success_failure(result: dict[str, Any]) -> None:
    previous = latest = _latest_result or _read_persisted_health() or {}
    if result["is_valid"]:
        result["last_success_at"] = result["last_checked_at"]
        result["last_failure_at"] = latest.get("last_failure_at")
    else:
        result["last_success_at"] = latest.get("last_success_at")
        result["last_failure_at"] = result["last_checked_at"]


def _set_latest_result(result: dict[str, Any]) -> None:
    global _latest_result
    _latest_result = dict(result)
    path = health_json_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        logger.info("Could not chmod Instagram health JSON; continuing.")


def _read_persisted_health() -> dict[str, Any] | None:
    path = health_json_path()
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _maybe_send_notification(result: dict[str, Any]) -> None:
    global _last_alert_at, _last_recovery_at, _previous_status
    status = str(result["status"])
    now = datetime.now(timezone.utc)
    if status in INVALID_STATUSES:
        settings = get_settings()
        cooldown = timedelta(hours=max(settings.instagram_cookie_alert_cooldown_hours, 1))
        if _last_alert_at is None or now - _last_alert_at >= cooldown:
            if _send_invalid_alert(result):
                _last_alert_at = now
    elif status == "valid" and _previous_status in INVALID_STATUSES:
        if _last_recovery_at is None or _last_recovery_at < _last_alert_at:
            if _send_recovery_alert(result):
                _last_recovery_at = now
    _previous_status = status


def _send_invalid_alert(result: dict[str, Any]) -> bool:
    settings = get_settings()
    body = f"""Instagram cookies appear to be expired, invalid, missing, or empty.

Current status: {result['status']}
Last checked: {result['last_checked_at']}
Cookie file path: {instagram_cookie_path()}
Cookie file size: {result['cookie_file_size']} bytes
Reason: {result['message']}
Technical reason: {result.get('technical_reason') or 'n/a'}
Admin panel: {settings.admin_panel_url or 'Not configured'}

Please export fresh Instagram cookies from a logged-in browser and upload the
new instagram_cookies.txt file from the ApexLoad admin panel.

The old cookie file was not changed unless a valid replacement was uploaded.
"""
    return send_admin_email("ApexLoad Alert: Instagram cookies need refresh", body)


def _send_recovery_alert(result: dict[str, Any]) -> bool:
    body = f"""Instagram cookies are valid again.

Current status: {result['status']}
Last checked: {result['last_checked_at']}
Message: {result['message']}
"""
    return send_admin_email("ApexLoad Recovery: Instagram cookies are valid again", body)


def _looks_like_expired(value: str) -> bool:
    return any(
        marker in value
        for marker in (
            "login required",
            "rate-limit",
            "rate limit",
            "not available",
            "empty media response",
            "checkpoint",
            "challenge",
            "cookies",
            "cookie",
            "http error 401",
            "http error 403",
        )
    )


def _safe_text(value: str) -> str:
    return " ".join(value.split())[:500] or "yt-dlp validation failed."


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_from_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
