import logging
from typing import Any

from app.core.config import get_settings
from app.services.instagram_auth_service import (
    InstagramAuthError,
    instagram_cookie_path,
    validate_instagram_cookie_file,
)

logger = logging.getLogger("apexload.ytdlp_options")


class _SafeTikTokYtDlpLogger:
    """Prevent yt-dlp from emitting raw TikTok URLs or extractor text."""

    def debug(self, _message: str) -> None:
        return None

    def warning(self, _message: str) -> None:
        logger.debug("TikTok yt-dlp warning captured by recovery executor.")

    def error(self, _message: str) -> None:
        logger.debug("TikTok yt-dlp error captured by recovery executor.")


_SAFE_TIKTOK_LOGGER = _SafeTikTokYtDlpLogger()


def build_ytdlp_options(
    platform: str,
    purpose: str,
    extra_opts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    debug_mode = settings.environment.lower() != "production"
    options: dict[str, Any] = {
        "quiet": not debug_mode,
        "no_warnings": not debug_mode,
        "ignoreerrors": False,
        "retries": 3,
        "socket_timeout": 20,
        "extractor_retries": 3,
        "noplaylist": True,
    }
    if purpose in {"download", "image_download"}:
        options["restrictfilenames"] = True
    if purpose in {"analyze", "validate", "metadata", "image_metadata"}:
        options["skip_download"] = True
        options["download"] = False
        options["extract_flat"] = False
    if settings.ffmpeg_location:
        options["ffmpeg_location"] = settings.ffmpeg_location

    if platform == "Instagram":
        options.update(_instagram_auth_options())
        impersonate_target = build_impersonate_target("chrome")
        if impersonate_target is not None:
            options["impersonate"] = impersonate_target
    elif platform == "TikTok":
        options["logger"] = _SAFE_TIKTOK_LOGGER
        impersonate_target = build_supported_impersonate_target("chrome")
        if impersonate_target is not None:
            options["impersonate"] = impersonate_target
    if extra_opts:
        options.update(extra_opts)
    return options


def _instagram_auth_options() -> dict[str, Any]:
    settings = get_settings()
    mode = (settings.instagram_auth_mode or "none").lower()
    if mode == "cookiefile":
        cookie_path = instagram_cookie_path()
        valid, reason = validate_instagram_cookie_file(cookie_path)
        if not valid:
            logger.info("Instagram cookiefile auth unavailable: %s", reason)
            raise InstagramAuthError("Instagram cookie file is missing on the server.")
        return {"cookiefile": str(cookie_path)}
    if mode == "browser":
        if not settings.ytdlp_cookies_from_browser_enable:
            logger.info("Instagram browser auth mode enabled but cookies-from-browser is disabled")
            raise InstagramAuthError("Instagram browser cookies are disabled.")
        values: list[str] = [settings.ytdlp_cookies_browser or "chrome"]
        if settings.ytdlp_cookies_browser_profile:
            values.append(settings.ytdlp_cookies_browser_profile)
        if settings.ytdlp_cookies_browser_keyring:
            while len(values) < 2:
                values.append("")
            values.append(settings.ytdlp_cookies_browser_keyring)
        return {"cookiesfrombrowser": tuple(values)}
    if mode == "none":
        logger.info("Instagram auth mode is none; yt-dlp will run without cookies.")
        return {}
    logger.info("Unknown Instagram auth mode: %s", mode)
    return {}


def configured_instagram_cookiefile() -> str | None:
    settings = get_settings()
    if (settings.instagram_auth_mode or "").lower() != "cookiefile":
        return None
    cookie_path = instagram_cookie_path()
    valid, _reason = validate_instagram_cookie_file(cookie_path)
    return str(cookie_path) if valid else None


def build_impersonate_target(value: str):
    if not value:
        return None
    try:
        from yt_dlp.networking.impersonate import ImpersonateTarget
    except Exception:
        logger.info("yt-dlp ImpersonateTarget is unavailable.")
        return None

    try:
        if hasattr(ImpersonateTarget, "from_str"):
            return ImpersonateTarget.from_str(value)
        return ImpersonateTarget(client=value)
    except (TypeError, ValueError):
        logger.info("Invalid yt-dlp impersonation target requested. target=%s", value)
        return None


def build_supported_impersonate_target(value: str):
    """Return an impersonation target only when curl-cffi supports it."""
    target = build_impersonate_target(value)
    if target is None:
        return None
    try:
        from yt_dlp.networking._curlcffi import CurlCFFIRH
    except Exception:
        logger.info(
            "yt-dlp curl-cffi impersonation is unavailable. target=%s",
            value,
        )
        return None

    if any(target in supported for supported in CurlCFFIRH.supported_targets):
        return target
    logger.info("yt-dlp impersonation target is unsupported. target=%s", value)
    return None
