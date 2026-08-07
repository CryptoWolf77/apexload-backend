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
    *,
    anonymous_youtube: bool = False,
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
        if not anonymous_youtube:
            options.update(_youtube_auth_options())
        options["js_runtimes"] = {"deno": {}}
        # YouTube answers the default web player with "The page needs to be
        # reloaded" when it wants a session/PO token we do not have. yt-dlp
        # walks this list in order until a player returns a usable response,
        # and the TV/mobile players do not take that code path.
        player_clients = (
            [settings.youtube_player_client]
            if anonymous_youtube
            else ["tv", "web_safari", "android_vr", "web"]
        )
        options["extractor_args"] = {
            "youtube": {"player_client": player_clients},
            "youtubepot-bgutilhttp": {"base_url": [settings.bgutil_base_url]},
        }

    if extra_opts:
        options.update(extra_opts)
    return options


def apply_anonymous_youtube_proxy(options: dict[str, Any], proxy_url: str) -> None:
    """Attach a public proxy only to this yt-dlp call, never to credentials."""
    settings = get_settings()
    options.pop("cookiefile", None)
    options.pop("cookiesfrombrowser", None)
    options["proxy"] = proxy_url
    options["extractor_args"] = {
        "youtube": {"player_client": [settings.youtube_player_client]},
        "youtubepot-bgutilhttp": {"base_url": [settings.bgutil_base_url]},
    }


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
