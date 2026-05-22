import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger("apexload.instagram_auth")

DEFAULT_INSTAGRAM_TEST_URL = "https://www.instagram.com/reel/DYcGKp2hwA1/"

_last_validation_status = "unknown"
_last_validation_reason = "Not validated yet"


class InstagramAuthError(RuntimeError):
    pass


def instagram_cookie_path() -> Path:
    settings = get_settings()
    raw_path = settings.instagram_cookie_file or settings.instagram_cookies_file
    return Path(raw_path).expanduser().resolve()


def validate_instagram_cookie_file(path: Path | None = None) -> tuple[bool, str]:
    cookie_path = path or instagram_cookie_path()
    if not cookie_path.is_file():
        return False, "Instagram cookie file is missing on the server."
    if cookie_path.stat().st_size <= 0:
        return False, "Instagram cookie file is empty."

    try:
        with cookie_path.open("r", encoding="utf-8", errors="ignore") as file:
            rows = [line.strip() for line in file]
    except OSError:
        return False, "Instagram cookie file could not be read."

    non_comment_rows = []
    for row in rows:
        if row.startswith("#HttpOnly_"):
            row = row.removeprefix("#HttpOnly_")
        if row and not row.startswith("#"):
            non_comment_rows.append(row)

    if not non_comment_rows:
        return False, "Cookie file has no Netscape cookie rows."
    if any(
        ("instagram.com" in row.lower() or ".instagram.com" in row.lower())
        and len(row.split("\t")) >= 7
        for row in non_comment_rows
    ):
        return True, "Cookie file looks valid"
    return False, "Cookie file does not contain Instagram Netscape cookie rows."


def save_instagram_cookie_file_securely(content: str) -> dict[str, Any]:
    cookie_path = instagram_cookie_path()
    valid, reason = _validate_cookie_text(content)
    if not valid:
        return _status_with_validation(False, reason)

    cookie_path.parent.mkdir(parents=True, exist_ok=True)
    cookie_path.write_text(content, encoding="utf-8")
    try:
        os.chmod(cookie_path, 0o600)
    except OSError:
        logger.info("Could not chmod Instagram cookie file; continuing.")

    valid, reason = validate_instagram_cookie_file(cookie_path)
    return _status_with_validation(valid, reason)


def delete_instagram_cookie_file() -> dict[str, Any]:
    cookie_path = instagram_cookie_path()
    if cookie_path.exists():
        cookie_path.unlink()
    return _status_with_validation(False, "Cookie file deleted.")


def get_instagram_auth_status() -> dict[str, Any]:
    settings = get_settings()
    cookie_path = instagram_cookie_path()
    exists = cookie_path.is_file()
    size = cookie_path.stat().st_size if exists else 0
    valid, reason = validate_instagram_cookie_file(cookie_path) if exists else (
        False,
        "Instagram cookie file is missing on the server.",
    )
    return {
        "authMode": settings.instagram_auth_mode,
        "cookieFileConfigured": bool(settings.instagram_cookie_file),
        "cookieFileRaw": settings.instagram_cookie_file,
        "cookieFileResolved": str(cookie_path),
        "cookieFileExists": exists,
        "cookieFileSize": size,
        "cookieFileLooksValid": valid,
        "lastUpdated": _last_updated(cookie_path) if exists else None,
        "lastValidationStatus": _last_validation_status,
        "reason": _last_validation_reason if _last_validation_status != "unknown" else reason,
        "cookiesFromBrowserEnabled": settings.ytdlp_cookies_from_browser_enable,
        "browser": settings.ytdlp_cookies_browser,
        "ffmpegFound": shutil.which("ffmpeg") is not None,
        "ffprobeFound": shutil.which("ffprobe") is not None,
        "ytDlpVersion": yt_dlp_version(),
    }


def test_instagram_cookie_with_ytdlp(test_url: str | None = None) -> dict[str, Any]:
    import yt_dlp

    url = test_url or DEFAULT_INSTAGRAM_TEST_URL
    from app.services.ytdlp_options import build_ytdlp_options

    try:
        opts = build_ytdlp_options("Instagram", "validate", {"skip_download": True})
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        title = info.get("title") if isinstance(info, dict) else None
        return _status_with_validation(
            True,
            "Instagram cookies are valid",
            extra={"title": title, "ytDlpVersion": yt_dlp_version()},
        )
    except Exception as exc:
        logger.warning("Instagram cookie validation failed: %s", _safe_error(exc))
        return _status_with_validation(
            False,
            "Instagram cookies failed validation",
            extra={"error": _safe_error(exc), "ytDlpVersion": yt_dlp_version()},
        )


def ytdlp_auth_debug_status() -> dict[str, Any]:
    settings = get_settings()
    status = get_instagram_auth_status()
    return {
        "ytDlpVersion": yt_dlp_version(),
        "ffmpegFound": shutil.which("ffmpeg") is not None,
        "ffprobeFound": shutil.which("ffprobe") is not None,
        "instagramAuthMode": settings.instagram_auth_mode,
        "cookieFileExists": status["cookieFileExists"],
        "cookieFileLooksValid": status["cookieFileLooksValid"],
        "cookiesFromBrowserEnabled": settings.ytdlp_cookies_from_browser_enable,
        "browser": settings.ytdlp_cookies_browser,
    }


def yt_dlp_version() -> str:
    try:
        import yt_dlp

        return str(yt_dlp.version.__version__)
    except Exception:
        return "unknown"


def _validate_cookie_text(content: str) -> tuple[bool, str]:
    if not content.strip():
        return False, "Cookie text is empty."
    rows = []
    for row in content.splitlines():
        normalized = row.strip()
        if normalized.startswith("#HttpOnly_"):
            normalized = normalized.removeprefix("#HttpOnly_")
        if normalized and not normalized.startswith("#"):
            rows.append(normalized)
    if not rows:
        return False, "Cookie text has no Netscape cookie rows."
    if any(
        ("instagram.com" in row.lower() or ".instagram.com" in row.lower())
        and len(row.split("\t")) >= 7
        for row in rows
    ):
        return True, "Cookie file looks valid"
    return False, "Cookie text does not contain Instagram Netscape cookie rows."


def _status_with_validation(
    valid: bool,
    reason: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    global _last_validation_status, _last_validation_reason
    _last_validation_status = "valid" if valid else "invalid"
    _last_validation_reason = reason
    status = get_instagram_auth_status()
    status["lastValidationStatus"] = _last_validation_status
    status["reason"] = reason
    if extra:
        status.update(extra)
    return status


def _last_updated(cookie_path: Path) -> str:
    timestamp = datetime.fromtimestamp(cookie_path.stat().st_mtime, tz=timezone.utc)
    return timestamp.isoformat()


def _safe_error(exc: Exception) -> str:
    message = " ".join(str(exc).split())
    return message[:240] or "Instagram authentication failed"
