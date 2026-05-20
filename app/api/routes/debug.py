from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import get_settings
from app.services.ytdlp_analyze_service import (
    AnalyzeServiceError,
    UnsupportedUrlError,
    YtDlpAnalyzeService,
)

router = APIRouter(tags=["debug"])
instagram_service = YtDlpAnalyzeService()


class InstagramYtDlpDebugRequest(BaseModel):
    url: str


@router.get("/config")
async def config_debug() -> dict[str, bool | str]:
    # TODO: Remove or protect debug endpoints before production release.
    settings = get_settings()
    return {
        "enableInstagramCookies": settings.enable_instagram_cookies,
        "enableYoutubeCookies": settings.enable_youtube_cookies,
        "useMockAnalyzeFallback": settings.use_mock_analyze_fallback,
        "instagramCookiesFile": settings.instagram_cookies_file,
        "youtubeCookiesFile": settings.youtube_cookies_file,
    }


@router.get("/instagram-cookies")
async def instagram_cookies_debug() -> dict[str, bool | int | str | None]:
    # TODO: Remove or protect debug endpoints before production release.
    status = instagram_service.instagram_cookie_status()
    cookie_path = (
        Path(str(status["cookieFileResolved"]))
        if status["cookieFileResolved"]
        else None
    )
    valid, reason = _validate_cookie_file(
        cookie_path if status["cookieFileExists"] else None,
        int(status["cookieFileSize"]),
    )

    return {
        "success": True,
        "enableInstagramCookies": status["enableInstagramCookies"],
        "cookieFileRaw": status["cookieFileRaw"],
        "cookieFileResolved": status["cookieFileResolved"],
        "cookieFileExists": status["cookieFileExists"],
        "cookieFileSize": status["cookieFileSize"],
        "cookieFileValid": bool(status["cookieFileValid"] and valid),
        "cookieValidationReason": reason,
    }


@router.get("/instagram-image")
async def instagram_image_debug(
    url: str,
) -> dict[str, bool | int | str | None]:
    # TODO: Remove or protect debug endpoints before production release.
    debug = instagram_service.debug_instagram_image_extraction(url)
    return {
        "success": debug["success"],
        "url": debug["url"],
        "cookiesEnabled": debug["cookiesEnabled"],
        "cookieFileExists": debug["cookieFileExists"],
        "htmlStatus": debug["htmlStatus"],
        "finalUrl": debug["finalUrl"],
        "htmlLength": debug["htmlLength"],
        "foundOgImage": debug["foundOgImage"],
        "foundDisplayUrl": debug["foundDisplayUrl"],
        "foundCdnUrlsCount": debug["foundCdnUrlsCount"],
        "rejectedStaticAssetsCount": debug["rejectedStaticAssetsCount"],
        "rejectedSmallImagesCount": debug["rejectedSmallImagesCount"],
        "acceptedCandidateSize": debug["acceptedCandidateSize"],
        "bestImageUrlMasked": debug["bestImageUrlMasked"],
        "reason": debug["reason"],
    }


@router.get("/x-image")
async def x_image_debug(
    url: str,
) -> dict[str, bool | int | str | None]:
    # TODO: Remove or protect debug endpoints before production release.
    debug = instagram_service.debug_x_image_extraction(url)
    return {
        "success": debug["success"],
        "url": debug["url"],
        "htmlStatus": debug["htmlStatus"],
        "finalUrl": debug["finalUrl"],
        "htmlLength": debug["htmlLength"],
        "foundOgImage": debug["foundOgImage"],
        "foundTwitterImage": debug["foundTwitterImage"],
        "foundPbsMediaCount": debug["foundPbsMediaCount"],
        "rejectedSmallImagesCount": debug["rejectedSmallImagesCount"],
        "acceptedCandidateSize": debug["acceptedCandidateSize"],
        "bestImageUrlMasked": debug["bestImageUrlMasked"],
        "reason": debug["reason"],
    }


@router.get("/youtube-cookies")
async def youtube_cookies_debug() -> dict[str, bool | int | str | None]:
    # TODO: Remove or protect debug endpoints before production release.
    settings = get_settings()
    raw_path = settings.youtube_cookies_file
    resolved_path = str(Path(raw_path).expanduser().resolve()) if raw_path else ""
    cookie_path = Path(resolved_path) if resolved_path else None
    exists = bool(cookie_path and cookie_path.is_file())
    size = cookie_path.stat().st_size if cookie_path and exists else 0
    valid, reason = _validate_cookie_file(
        cookie_path if exists else None,
        size,
        domain="youtube.com",
    )

    return {
        "success": True,
        "enableYoutubeCookies": settings.enable_youtube_cookies,
        "cookieFileRaw": raw_path,
        "cookieFileResolved": resolved_path,
        "cookieFileExists": exists,
        "cookieFileSize": size,
        "cookieFileValid": valid,
        "cookieValidationReason": reason,
    }


@router.post("/test-instagram-ytdlp")
async def test_instagram_ytdlp(
    payload: InstagramYtDlpDebugRequest,
) -> dict[str, bool | int | float | str | None]:
    # TODO: Remove or protect debug endpoints before production release.
    cookie_status = instagram_service.instagram_cookie_status()
    try:
        info = instagram_service.test_instagram_ytdlp_with_cookies(payload.url)
        media_info = instagram_service._select_media_info(info)
        return {
            "success": True,
            "source": "yt_dlp_cookies_direct",
            "title": media_info.get("title"),
            "duration": media_info.get("duration"),
            "thumbnail": media_info.get("thumbnail"),
            "extractor": media_info.get("extractor") or media_info.get("extractor_key"),
        }
    except (AnalyzeServiceError, UnsupportedUrlError) as exc:
        return {
            "success": False,
            "source": "yt_dlp_cookies_direct",
            "error": getattr(exc, "raw_message", str(exc)),
            "cookieFileResolved": cookie_status["cookieFileResolved"],
            "cookieFileExists": cookie_status["cookieFileExists"],
            "cookieFileValid": cookie_status["cookieFileValid"],
        }


def _validate_cookie_file(
    cookie_path: Path | None,
    size: int,
    domain: str = "instagram.com",
) -> tuple[bool, str]:
    if cookie_path is None:
        return False, "Cookie file not found"
    if size <= 0:
        return False, "Cookie file is empty"

    try:
        with cookie_path.open("r", encoding="utf-8", errors="ignore") as file:
            sample_lines = [line.strip() for line in file]
    except OSError:
        return False, "Cookie file could not be read"

    non_empty_lines = []
    for line in sample_lines:
        if line.startswith("#HttpOnly_"):
            line = line.removeprefix("#HttpOnly_")
        if line and not line.startswith("#"):
            non_empty_lines.append(line)
    if not non_empty_lines:
        return False, "Cookie file has no cookie rows"

    has_instagram_cookie = any(
        domain in line.lower() and len(line.split("\t")) >= 7
        for line in non_empty_lines
    )
    if has_instagram_cookie:
        return True, "Cookie file looks valid"
    return False, f"Cookie file does not look like a Netscape {domain} cookie file"
