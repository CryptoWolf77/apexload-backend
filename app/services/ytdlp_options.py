import logging
from typing import Any

from app.core.config import get_settings
from app.services.instagram_auth_service import (
    InstagramAuthError,
    instagram_cookie_path,
    validate_instagram_cookie_file,
)
from app.services.youtube_auth_service import (
    YouTubeAuthError,
    get_youtube_auth_status,
    youtube_cookie_path,
    validate_youtube_cookie_file,
)

logger = logging.getLogger("apexload.ytdlp_options")


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
    elif platform == "YouTube Shorts":
        options.update(_youtube_auth_options())

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


def _youtube_auth_options() -> dict[str, Any]:
    status = get_youtube_auth_status()
    mode = str(status["authMode"] or "none").lower()
    if mode == "cookiefile":
        cookie_path = youtube_cookie_path()
        valid, reason = validate_youtube_cookie_file(cookie_path)
        if not valid:
            logger.info("YouTube cookiefile auth unavailable: %s", reason)
            raise YouTubeAuthError("YouTube cookie file is missing on the server.")
        return {"cookiefile": str(cookie_path)}
    if mode == "none":
        logger.info("YouTube auth mode is none; yt-dlp will run without cookies.")
        return {}
    logger.info("Unknown YouTube auth mode: %s", mode)
    return {}


def build_impersonate_target(value: str):
    if not value:
        return None
    try:
        from yt_dlp.networking.impersonate import ImpersonateTarget
    except Exception:
        logger.info("yt-dlp ImpersonateTarget is unavailable.")
        return None

    if hasattr(ImpersonateTarget, "from_str"):
        return ImpersonateTarget.from_str(value)
    return ImpersonateTarget(client=value)
