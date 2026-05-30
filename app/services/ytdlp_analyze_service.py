import ipaddress
import html
import logging
import re
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from app.core.config import get_settings
from app.models.analyze_models import AnalyzeResponse, FormatOption
from app.services.instagram_auth_service import (
    InstagramAuthError,
    get_instagram_auth_status,
)
from app.services.ytdlp_options import (
    build_ytdlp_options,
    configured_instagram_cookiefile,
)
from app.utils.platform_detector import detect_platform

logger = logging.getLogger("apexload.analyze")

SUPPORTED_HOSTS = (
    "tiktok.com",
    "instagram.com",
    "instagr.am",
    "facebook.com",
    "fb.watch",
    "twitter.com",
    "x.com",
    "youtube.com",
    "youtu.be",
    "pinterest.com",
    "pin.it",
    "reddit.com",
    "snapchat.com",
    "snap.com",
)

INSTAGRAM_WEB_APP_ID = "936619743392459"
INSTAGRAM_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.instagram.com/",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
MIN_INSTAGRAM_POST_IMAGE_BYTES = 10 * 1024
MIN_POST_IMAGE_BYTES = 10 * 1024
INSTAGRAM_PHOTO_UNAVAILABLE_MESSAGE = (
    "Instagram photo posts are not available for this link. Try a Reel/video link."
)
X_BROWSER_HEADERS = {
    "User-Agent": INSTAGRAM_BROWSER_HEADERS["User-Agent"],
    "Referer": "https://x.com/",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class AnalyzeServiceError(Exception):
    error = "analyze_failed"
    message = "Could not analyze this link. Please try again."

    def __init__(self, message: str | None = None, raw_message: str | None = None):
        self.message = message or self.message
        self.raw_message = raw_message or self.message
        super().__init__(self.message)


class UnsupportedUrlError(AnalyzeServiceError):
    error = "unsupported_url"
    message = "This link is not supported yet."


class InstagramAuthRequiredError(AnalyzeServiceError):
    error = "instagram_requires_auth"
    message = (
        "Instagram requires a valid server-side session. Please refresh "
        "Instagram cookies from the admin panel."
    )


class YouTubeAuthRequiredError(AnalyzeServiceError):
    error = "youtube_requires_auth"
    message = (
        "YouTube requested sign-in verification. Please try another link or "
        "configure YouTube cookies."
    )


class YtDlpAnalyzeService:
    def analyze(self, url: str) -> AnalyzeResponse:
        normalized_url = self._validate_url(url)
        platform = detect_platform(normalized_url)
        if platform == "Unknown":
            logger.info("Unsupported URL platform: %s", normalized_url)
            raise UnsupportedUrlError()
        if platform == "Instagram":
            normalized_url = self._clean_instagram_url(normalized_url)

        logger.info("Analyze URL received. platform=%s", platform)
        if platform == "Instagram":
            info, source = self._extract_instagram_info(normalized_url)
        elif platform == "YouTube Shorts":
            info, source = self._extract_youtube_info(normalized_url)
        elif platform == "X/Twitter":
            info, source = self._extract_x_info(normalized_url)
        else:
            logger.info("yt-dlp analyze started")
            info = self._extract_info(normalized_url)
            source = "yt_dlp"
        media_info = self._select_media_info(info)
        media_type = self.detect_media_type(media_info, platform, normalized_url)
        logger.info("yt-dlp analyze success. mediaType=%s", media_type)

        thumbnail, image_source = self.extract_best_image_url_with_source(media_info)
        if platform == "Instagram" and media_type == "image":
            photo_debug = self.debug_instagram_photo_extraction(
                normalized_url,
                info,
                self._instagram_cookiefile(),
            )
            candidate = photo_debug.get("bestImageUrl")
            if isinstance(candidate, str) and candidate:
                thumbnail = candidate
                image_source = str(photo_debug.get("bestImageSource") or "instagram_photo")
            else:
                thumbnail = ""
                image_source = str(photo_debug.get("reason") or "none")
        elif not thumbnail:
            thumbnail = self._thumbnail(media_info)
            image_source = "thumbnail" if thumbnail else "none"
        if platform == "X/Twitter" and media_type == "image" and not thumbnail:
            thumbnail = self.extract_x_image_from_html_or_metadata(normalized_url)
            image_source = "x_html" if thumbnail else image_source
        if media_type == "image":
            logger.info("Image URL extracted from %s", image_source)
        response = AnalyzeResponse(
            success=True,
            source=source,
            platform=platform,
            mediaType=media_type,
            title=self._title(media_info),
            thumbnail=thumbnail,
            duration=self._duration(media_info) if media_type == "video" else None,
            formats=(
                self._video_formats(media_info, thumbnail, platform)
                if media_type == "video"
                else self._image_formats(
                    thumbnail,
                    unavailable_reason=(
                        INSTAGRAM_PHOTO_UNAVAILABLE_MESSAGE
                        if platform == "Instagram" and not thumbnail
                        else "No image URL found"
                    ),
                )
            ),
            message=(
                INSTAGRAM_PHOTO_UNAVAILABLE_MESSAGE
                if platform == "Instagram" and media_type == "image" and not thumbnail
                else "Image detected, but direct image URL was not found."
                if media_type == "image" and not thumbnail
                else None
            ),
        )
        logger.info("formats normalized. count=%s", len(response.formats))
        return response

    def _validate_url(self, url: str) -> str:
        value = url.strip()
        if not value:
            raise UnsupportedUrlError()

        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise UnsupportedUrlError()

        host = (parsed.hostname or "").lower()
        if host in {"localhost", "127.0.0.1", "::1"}:
            raise UnsupportedUrlError()

        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None
        if ip and (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved):
            raise UnsupportedUrlError()

        if not self._is_supported_host(host):
            raise UnsupportedUrlError()

        # TODO: Add stronger SSRF protection before production release,
        # including DNS resolution checks against private/internal networks.
        return value

    def _is_supported_host(self, host: str) -> bool:
        return any(
            host == domain or host.endswith(f".{domain}")
            for domain in SUPPORTED_HOSTS
        )

    def _clean_instagram_url(self, url: str) -> str:
        parsed = urlparse(url)
        path = parsed.path
        allowed_prefixes = ("/reel/", "/p/", "/tv/")
        if not path.startswith(allowed_prefixes):
            return url

        tracking_params = {"utm_source", "igsh", "fbclid"}
        query = urlencode(
            [
                (key, value)
                for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                if key not in tracking_params
            ]
        )
        return urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                path if path.endswith("/") else f"{path}/",
                "",
                query,
                "",
            )
        )

    def _instagram_cookiefile(self) -> str | None:
        return configured_instagram_cookiefile()

    def _youtube_cookiefile(self) -> str | None:
        settings = get_settings()
        if not settings.enable_youtube_cookies:
            logger.info("YouTube cookies disabled. Skipping cookie retry.")
            return None
        if not settings.youtube_cookies_file:
            logger.info("YouTube cookies enabled but cookie file not found: (empty)")
            return None
        cookie_path = Path(settings.youtube_cookies_file).expanduser().resolve()
        if not cookie_path.is_file():
            logger.info("YouTube cookies enabled but cookie file not found: %s", cookie_path)
            return None
        if not self._cookie_file_looks_valid(cookie_path, domain="youtube.com"):
            logger.info("YouTube cookies enabled but cookie file is not valid: %s", cookie_path)
            return None
        return str(cookie_path)

    def instagram_cookie_status(self) -> dict[str, bool | int | str]:
        status = get_instagram_auth_status()
        return {
            "enableInstagramCookies": status["authMode"] == "cookiefile",
            "cookieFileRaw": status["cookieFileRaw"],
            "cookieFileResolved": status["cookieFileResolved"],
            "cookieFileExists": status["cookieFileExists"],
            "cookieFileSize": status["cookieFileSize"],
            "cookieFileValid": status["cookieFileLooksValid"],
        }

    def _log_instagram_cookie_settings(self) -> None:
        status = get_instagram_auth_status()
        logger.info(
            "INSTAGRAM_AUTH_MODE=%s",
            status["authMode"],
        )
        logger.info(
            "INSTAGRAM_COOKIE_FILE=%s",
            status["cookieFileRaw"],
        )
        logger.info(
            "Instagram cookie file resolved path=%s",
            status["cookieFileResolved"],
        )
        logger.info(
            "Instagram cookie file exists=%s",
            status["cookieFileExists"],
        )
        logger.info(
            "Instagram cookie file valid=%s",
            status["cookieFileLooksValid"],
        )

    def _cookie_file_looks_valid(
        self,
        cookie_path: Path,
        domain: str = "instagram.com",
    ) -> bool:
        if not cookie_path.is_file() or cookie_path.stat().st_size <= 0:
            return False
        try:
            with cookie_path.open("r", encoding="utf-8", errors="ignore") as file:
                for line in file:
                    normalized = line.strip().lower()
                    if normalized.startswith("#httponly_"):
                        normalized = normalized.removeprefix("#httponly_")
                    if not normalized or normalized.startswith("#"):
                        continue
                    if domain in normalized and len(line.split("\t")) >= 7:
                        return True
        except OSError:
            return False
        return False

    def _is_instagram_block_error(self, message: str) -> bool:
        text = message.lower()
        blocked_markers = (
            "empty media response",
            "login required",
            "cookies",
            "cookie",
            "rate-limit",
            "rate limited",
            "rate limit",
            "blocked",
            "api is not granting access",
            "this content isn't available to everyone",
            "can't be seen by certain audiences",
            "http error 400",
            "please wait a few minutes",
            "requested content is not available",
            "unable to extract",
            "http error 401",
            "http error 403",
            "checkpoint",
            "challenge",
            "server-side session",
            "cookie file is missing",
            "browser cookies are disabled",
        )
        return any(marker in text for marker in blocked_markers)

    def _is_youtube_auth_error(self, message: str) -> bool:
        text = message.lower()
        return any(
            marker in text
            for marker in (
                "sign in to confirm",
                "not a bot",
                "cookies",
                "login required",
                "confirm your age",
            )
        )

    def _short_error(self, message: str) -> str:
        compact = " ".join(message.split())
        return compact[:240]

    def _log_backend_instagram_debug(self) -> None:
        status = self.instagram_cookie_status()
        logger.info("BACKEND INSTAGRAM DEBUG:")
        logger.info(
            "INSTAGRAM_COOKIEFILE_AUTH=%s",
            status["enableInstagramCookies"],
        )
        logger.info("INSTAGRAM_COOKIE_FILE=%s", status["cookieFileRaw"])
        logger.info("resolved_cookie_path=%s", status["cookieFileResolved"])
        logger.info("cookie_file_exists=%s", status["cookieFileExists"])
        logger.info("cookie_file_valid=%s", status["cookieFileValid"])
        logger.info("starting direct cookiefile yt-dlp attempt")

    def test_instagram_ytdlp_with_cookies(self, url: str) -> dict:
        normalized_url = self._validate_url(url)
        platform = detect_platform(normalized_url)
        if platform != "Instagram":
            raise UnsupportedUrlError()
        clean_url = self._clean_instagram_url(normalized_url)
        try:
            return self._extract_info(clean_url)
        except InstagramAuthError as exc:
            raise InstagramAuthRequiredError(raw_message=str(exc)) from exc

    def _extract_instagram_info_with_direct_cookies(
        self,
        url: str,
        cookiefile: str,
    ) -> dict:
        self._log_backend_instagram_debug()
        try:
            import yt_dlp

            ydl_opts = {
                "quiet": False,
                "no_warnings": False,
                "skip_download": True,
                "cookiefile": cookiefile,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:
            raw_message = str(exc)
            logger.warning("Instagram direct cookiefile yt-dlp failure: %s", raw_message)
            raise AnalyzeServiceError(raw_message=raw_message) from exc

        if not isinstance(info, dict):
            raise AnalyzeServiceError(raw_message="yt-dlp returned an invalid response")
        return info

    def _extract_instagram_info(self, url: str) -> tuple[dict, str]:
        self._log_instagram_cookie_settings()
        settings = get_settings()
        auth_mode = settings.instagram_auth_mode
        cookiefile = configured_instagram_cookiefile()
        last_error: AnalyzeServiceError | None = None
        if auth_mode in {"cookiefile", "browser"}:
            attempts = (
                (
                    f"Instagram attempt 1: {auth_mode} auth started",
                    "yt_dlp_cookies" if auth_mode == "cookiefile" else "yt_dlp_browser",
                    {},
                ),
                (
                    f"Instagram attempt 2: {auth_mode} auth + app_id started",
                    (
                        "yt_dlp_cookies_app_id"
                        if auth_mode == "cookiefile"
                        else "yt_dlp_browser_app_id"
                    ),
                    {"extractor_args": {"instagram": {"app_id": [INSTAGRAM_WEB_APP_ID]}}},
                ),
                (
                    f"Instagram attempt 3: {auth_mode} auth + app_id + headers started",
                    (
                        "yt_dlp_cookies_app_id_headers"
                        if auth_mode == "cookiefile"
                        else "yt_dlp_browser_app_id_headers"
                    ),
                    {
                        "extractor_args": {
                            "instagram": {"app_id": [INSTAGRAM_WEB_APP_ID]}
                        },
                        "http_headers": INSTAGRAM_BROWSER_HEADERS,
                    },
                ),
            )

            for index, (log_message, source, extra_options) in enumerate(
                attempts,
                start=1,
            ):
                logger.info(log_message)
                try:
                    info = self._extract_info(url, extra_options=extra_options)
                    logger.info("Instagram attempt %s succeeded", index)
                    return info, source
                except InstagramAuthError as exc:
                    logger.info("Instagram auth setup failed: %s", exc)
                    raise InstagramAuthRequiredError(raw_message=str(exc)) from exc
                except AnalyzeServiceError as exc:
                    logger.info(
                        "Instagram attempt %s failed: %s",
                        index,
                        self._short_error(exc.raw_message),
                    )
                    last_error = exc

            html_info = self._extract_instagram_image_info_from_html(url, cookiefile)
            if html_info:
                logger.info("Instagram image analyze succeeded from webpage HTML")
                return html_info, "instagram_html"
            if self._is_instagram_image_post_url(url):
                logger.info("Instagram photo post has no validated image URL")
                return self._instagram_photo_unavailable_info(url), "instagram_photo_unavailable"
            raise InstagramAuthRequiredError(
                raw_message=last_error.raw_message if last_error else None
            ) from last_error

        logger.info("Instagram attempt 1: no cookies started")
        try:
            info = self._extract_info(url)
            logger.info("Instagram attempt 1 succeeded")
            return info, "yt_dlp"
        except AnalyzeServiceError as exc:
            last_error = exc
            logger.info(
                "Instagram attempt 1 failed: %s",
                self._short_error(exc.raw_message),
            )

        logger.info(
            "Instagram analyze failed without cookies. Retrying with cookies if enabled."
        )
        html_info = self._extract_instagram_image_info_from_html(url, None)
        if html_info:
            logger.info("Instagram image analyze succeeded from webpage HTML")
            return html_info, "instagram_html"
        if self._is_instagram_image_post_url(url):
            logger.info("Instagram photo post has no validated image URL")
            return self._instagram_photo_unavailable_info(url), "instagram_photo_unavailable"

        raise InstagramAuthRequiredError(
            raw_message=last_error.raw_message
        ) from last_error

    def _extract_youtube_info(self, url: str) -> tuple[dict, str]:
        try:
            return self._extract_info(url), "yt_dlp"
        except AnalyzeServiceError as exc:
            if not self._is_youtube_auth_error(exc.raw_message):
                raise
            cookiefile = self._youtube_cookiefile()
            if not cookiefile:
                raise YouTubeAuthRequiredError(raw_message=exc.raw_message) from exc
            try:
                return self._extract_info(url, cookiefile=cookiefile), "yt_dlp_cookies"
            except AnalyzeServiceError as cookie_exc:
                if self._is_youtube_auth_error(cookie_exc.raw_message):
                    raise YouTubeAuthRequiredError(
                        raw_message=cookie_exc.raw_message
                    ) from cookie_exc
                raise

    def _extract_x_info(self, url: str) -> tuple[dict, str]:
        try:
            return self._extract_info(url), "yt_dlp"
        except AnalyzeServiceError as exc:
            if not self._is_x_no_video_error(exc.raw_message):
                raise
            html_info = self._extract_x_image_info_from_html(url)
            if html_info:
                logger.info("X/Twitter image analyze succeeded from webpage HTML")
                return html_info, "x_html"
            raise

    def _extract_info(
        self,
        url: str,
        cookiefile: str | None = None,
        extra_options: dict | None = None,
    ) -> dict:
        try:
            import yt_dlp

            platform = detect_platform(url)
            options = build_ytdlp_options(platform, "analyze")
            if cookiefile:
                options["cookiefile"] = cookiefile
            if extra_options:
                options.update(extra_options)
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
        except InstagramAuthError:
            raise
        except UnsupportedUrlError:
            raise
        except Exception as exc:
            raw_message = str(exc)
            logger.warning("yt-dlp analyze failure: %s", raw_message)
            raise AnalyzeServiceError(raw_message=raw_message) from exc

        if not isinstance(info, dict):
            raise AnalyzeServiceError()
        return info

    def _is_x_no_video_error(self, message: str) -> bool:
        text = message.lower()
        return "twitter" in text and (
            "no video could be found" in text
            or "no video found" in text
            or "no video formats" in text
        )

    def _extract_instagram_image_info_from_html(
        self,
        url: str,
        cookiefile: str | None,
    ) -> dict | None:
        if not self._is_instagram_image_post_url(url):
            return None
        image_url = self.extract_instagram_image_from_html(url, cookiefile)
        if not image_url:
            return None
        display_id = self._instagram_display_id(url)
        return {
            "id": display_id,
            "display_id": display_id,
            "title": f"Instagram photo {display_id}" if display_id else "Instagram photo",
            "thumbnail": image_url,
            "url": image_url,
            "ext": self._image_ext_from_url(image_url),
            "_type": "image",
        }

    def _instagram_photo_unavailable_info(self, url: str) -> dict:
        display_id = self._instagram_display_id(url)
        return {
            "id": display_id,
            "display_id": display_id,
            "title": f"Instagram photo {display_id}" if display_id else "Instagram photo",
            "thumbnail": "",
            "url": "",
            "ext": "jpg",
            "_type": "image",
        }

    def _extract_x_image_info_from_html(self, url: str) -> dict | None:
        image_url = self.extract_x_image_from_html_or_metadata(url)
        if not image_url:
            return None
        display_id = self._x_status_id(url)
        return {
            "id": display_id,
            "display_id": display_id,
            "title": f"X image post {display_id}" if display_id else "X image post",
            "thumbnail": image_url,
            "url": image_url,
            "ext": self._image_ext_from_url(image_url),
            "_type": "image",
        }

    def extract_x_image_from_html_or_metadata(
        self,
        url: str,
        cookies_file: str | None = None,
    ) -> str | None:
        debug = self.debug_x_image_extraction(url, cookies_file)
        image_url = debug.get("bestImageUrl")
        if isinstance(image_url, str) and image_url:
            logger.info(
                "X/Twitter image extraction source: %s",
                debug.get("bestImageSource") or "unknown",
            )
            return image_url
        reason = debug.get("reason")
        if reason:
            logger.info("X/Twitter image extraction failed: %s", reason)
        return None

    def debug_x_image_extraction(
        self,
        url: str,
        cookies_file: str | None = None,
    ) -> dict[str, bool | int | str | None]:
        result: dict[str, bool | int | str | None] = {
            "success": False,
            "url": url,
            "htmlStatus": None,
            "htmlLength": 0,
            "finalUrl": None,
            "foundOgImage": False,
            "foundTwitterImage": False,
            "foundPbsMediaCount": 0,
            "rejectedSmallImagesCount": 0,
            "acceptedCandidateSize": None,
            "bestImageUrl": None,
            "bestImageUrlMasked": None,
            "bestImageSource": None,
            "reason": None,
        }

        if detect_platform(url) != "X/Twitter":
            result["reason"] = "URL is not an X/Twitter post"
            return result

        html_result = self._fetch_x_html(url, cookies_file)
        result.update(
            {
                "htmlStatus": html_result["status"],
                "htmlLength": html_result["htmlLength"],
                "finalUrl": html_result["finalUrl"],
            }
        )
        body = html_result["body"]
        if not isinstance(body, str) or not body:
            result["reason"] = str(html_result["reason"] or "X/Twitter HTML was empty")
            self._log_x_image_debug(result)
            return result

        candidates = self._extract_x_image_candidates(body)
        result["foundOgImage"] = any(source == "og:image" for source, _url in candidates)
        result["foundTwitterImage"] = any(
            source == "twitter:image" for source, _url in candidates
        )
        result["foundPbsMediaCount"] = len(
            {
                candidate
                for _source, candidate in candidates
                if "pbs.twimg.com/media" in candidate.lower()
            }
        )

        for source, candidate in sorted(
            candidates,
            key=lambda item: self._x_candidate_priority(item[1]),
            reverse=True,
        ):
            validation = self._validate_image_candidate(
                candidate,
                referer="https://x.com/",
            )
            if validation["valid"]:
                result.update(
                    {
                        "success": True,
                        "bestImageUrl": candidate,
                        "bestImageUrlMasked": self._mask_url(candidate),
                        "bestImageSource": source,
                        "acceptedCandidateSize": validation["size"],
                        "reason": "Image URL validated",
                    }
                )
                self._log_x_image_debug(result)
                return result
            if validation["reason"] == "small_image":
                result["rejectedSmallImagesCount"] = int(
                    result["rejectedSmallImagesCount"] or 0
                ) + 1

        result["reason"] = "Could not find a downloadable image for this X post."
        self._log_x_image_debug(result)
        return result

    def extract_instagram_image_from_html(
        self,
        url: str,
        cookies_file: str | None = None,
    ) -> str | None:
        debug = self.debug_instagram_image_extraction(url, cookies_file)
        image_url = debug.get("bestImageUrl")
        if isinstance(image_url, str) and image_url:
            logger.info(
                "Instagram image extraction source: %s",
                debug.get("bestImageSource") or "unknown",
            )
            return image_url
        reason = debug.get("reason")
        if reason:
            logger.info("Instagram image extraction failed: %s", reason)
        return None

    def debug_instagram_photo_extraction(
        self,
        url: str,
        info: dict | None = None,
        cookies_file: str | None = None,
    ) -> dict[str, bool | int | str | None]:
        status = self.instagram_cookie_status()
        cookies_enabled = bool(status["enableInstagramCookies"])
        cookie_file_exists = bool(status["cookieFileExists"])
        cookiefile = cookies_file
        if cookies_enabled and not cookiefile:
            resolved = str(status["cookieFileResolved"])
            cookiefile = resolved if resolved and cookie_file_exists else None

        result: dict[str, bool | int | str | None] = {
            "success": False,
            "url": url,
            "mediaType": "image" if self._is_instagram_image_post_url(url) else None,
            "foundCandidateCount": 0,
            "rejectedStaticCount": 0,
            "rejectedSmallCount": 0,
            "acceptedCandidateSize": None,
            "bestImageUrl": None,
            "bestImageUrlMasked": None,
            "bestImageSource": None,
            "reason": None,
        }
        if not self._is_instagram_image_post_url(url):
            result["reason"] = "URL is not an Instagram photo post"
            return result

        candidates: list[tuple[str, str]] = []
        if info:
            candidates.extend(self._extract_instagram_metadata_image_candidates(info))

        html_debug = self.debug_instagram_image_extraction(url, cookiefile)
        html_candidate = html_debug.get("bestImageUrl")
        if isinstance(html_candidate, str) and html_candidate:
            candidates.append(
                (
                    str(html_debug.get("bestImageSource") or "webpage_html"),
                    html_candidate,
                )
            )
        elif not candidates and html_debug.get("reason"):
            result["reason"] = str(html_debug["reason"])

        candidates = self._dedupe_candidates(candidates)
        result["foundCandidateCount"] = len(candidates)
        for source, candidate in sorted(
            candidates,
            key=lambda item: self._candidate_priority(item[1]),
            reverse=True,
        ):
            if self._is_instagram_static_asset_url(candidate):
                result["rejectedStaticCount"] = int(result["rejectedStaticCount"] or 0) + 1
                continue
            validation = self._validate_image_candidate(candidate)
            if validation["valid"]:
                result.update(
                    {
                        "success": True,
                        "bestImageUrl": candidate,
                        "bestImageUrlMasked": self._mask_url(candidate),
                        "bestImageSource": source,
                        "acceptedCandidateSize": validation["size"],
                        "reason": "Image URL validated",
                    }
                )
                return result
            if validation["reason"] == "small_image":
                result["rejectedSmallCount"] = int(result["rejectedSmallCount"] or 0) + 1

        result["reason"] = INSTAGRAM_PHOTO_UNAVAILABLE_MESSAGE
        return result

    def debug_instagram_image_extraction(
        self,
        url: str,
        cookies_file: str | None = None,
    ) -> dict[str, bool | int | str | None]:
        status = self.instagram_cookie_status()
        cookies_enabled = bool(status["enableInstagramCookies"])
        cookie_file_exists = bool(status["cookieFileExists"])
        cookiefile = cookies_file
        if cookies_enabled and not cookiefile:
            resolved = str(status["cookieFileResolved"])
            cookiefile = resolved if resolved and cookie_file_exists else None

        result: dict[str, bool | int | str | None] = {
            "success": False,
            "url": url,
            "cookiesEnabled": cookies_enabled,
            "cookieFileExists": cookie_file_exists,
            "htmlStatus": None,
            "htmlLength": 0,
            "finalUrl": None,
            "foundOgImage": False,
            "foundDisplayUrl": False,
            "foundCdnUrlsCount": 0,
            "rejectedStaticAssetsCount": 0,
            "rejectedSmallImagesCount": 0,
            "acceptedCandidateSize": None,
            "bestImageUrl": None,
            "bestImageUrlMasked": None,
            "bestImageSource": None,
            "reason": None,
        }

        if not self._is_instagram_image_post_url(url):
            result["reason"] = "URL is not an Instagram image post"
            return result

        html_result = self._fetch_instagram_html(url, cookiefile)
        result.update(
            {
                "htmlStatus": html_result["status"],
                "htmlLength": html_result["htmlLength"],
                "finalUrl": html_result["finalUrl"],
            }
        )
        body = html_result["body"]
        if not isinstance(body, str) or not body:
            result["reason"] = str(html_result["reason"] or "Instagram HTML was empty")
            self._log_instagram_image_debug(result)
            return result

        candidates = self._extract_instagram_image_candidates(body)
        result["foundOgImage"] = any(source == "og:image" for source, _url in candidates)
        result["foundDisplayUrl"] = any(
            source in {"display_url", "thumbnail_src"} for source, _url in candidates
        )
        result["foundCdnUrlsCount"] = len(
            {
                candidate
                for _source, candidate in candidates
                if self._looks_like_instagram_cdn_url(candidate)
                and not self._is_instagram_static_asset_url(candidate)
            }
        )

        for source, candidate in sorted(
            candidates,
            key=lambda item: self._candidate_priority(item[1]),
            reverse=True,
        ):
            if self._is_instagram_static_asset_url(candidate):
                result["rejectedStaticAssetsCount"] = int(
                    result["rejectedStaticAssetsCount"] or 0
                ) + 1
                continue

            validation = self._validate_image_candidate(candidate)
            if validation["valid"]:
                result.update(
                    {
                        "success": True,
                        "bestImageUrl": candidate,
                        "bestImageUrlMasked": self._mask_url(candidate),
                        "bestImageSource": source,
                        "acceptedCandidateSize": validation["size"],
                        "reason": "Image URL validated",
                    }
                )
                self._log_instagram_image_debug(result)
                return result
            if validation["reason"] == "small_image":
                result["rejectedSmallImagesCount"] = int(
                    result["rejectedSmallImagesCount"] or 0
                ) + 1

        result["reason"] = (
            "Could not find a downloadable image for this post. "
            "Try refreshing Instagram cookies."
        )
        self._log_instagram_image_debug(result)
        return result

    def _fetch_instagram_html(
        self,
        url: str,
        cookies_file: str | None,
    ) -> dict[str, int | str | None]:
        headers = dict(INSTAGRAM_BROWSER_HEADERS)
        cookie_header = self._cookie_header_from_netscape_file(cookies_file)
        if cookie_header:
            headers["Cookie"] = cookie_header

        try:
            request = Request(url, headers=headers)
            with urlopen(request, timeout=20) as response:
                body = response.read().decode("utf-8", errors="ignore")
                return {
                    "status": int(getattr(response, "status", 200)),
                    "finalUrl": response.geturl(),
                    "htmlLength": len(body),
                    "body": body,
                    "reason": None,
                }
        except Exception as exc:
            return {
                "status": None,
                "finalUrl": None,
                "htmlLength": 0,
                "body": "",
                "reason": self._short_error(str(exc)),
            }

    def _fetch_x_html(
        self,
        url: str,
        cookies_file: str | None,
    ) -> dict[str, int | str | None]:
        headers = dict(X_BROWSER_HEADERS)
        cookie_header = self._cookie_header_from_netscape_file(cookies_file, "x.com")
        if cookie_header:
            headers["Cookie"] = cookie_header

        try:
            request = Request(url, headers=headers)
            with urlopen(request, timeout=20) as response:
                body = response.read().decode("utf-8", errors="ignore")
                return {
                    "status": int(getattr(response, "status", 200)),
                    "finalUrl": response.geturl(),
                    "htmlLength": len(body),
                    "body": body,
                    "reason": None,
                }
        except Exception as exc:
            return {
                "status": None,
                "finalUrl": None,
                "htmlLength": 0,
                "body": "",
                "reason": self._short_error(str(exc)),
            }

    def _extract_instagram_image_candidates(self, body: str) -> list[tuple[str, str]]:
        candidates: list[tuple[str, str]] = []

        og_image = self._extract_og_image(body)
        if og_image:
            candidates.append(("og:image", og_image))

        for source, pattern in (
            ("display_url", r'"display_url"\s*:\s*"([^"]+)"'),
            ("display_resources", r'"display_resources"[\s\S]{0,4000}?"src"\s*:\s*"([^"]+)"'),
            ("thumbnail_src", r'"thumbnail_src"\s*:\s*"([^"]+)"'),
            ("shortcode_media", r'"(?:xdt_shortcode_media|shortcode_media)"[\s\S]{0,6000}?"display_url"\s*:\s*"([^"]+)"'),
            ("sidecar_media", r'"(?:carousel_media|edge_sidecar_to_children)"[\s\S]{0,8000}?"(?:display_url|src|url)"\s*:\s*"([^"]+)"'),
            ("graph_image", r'"(?:GraphImage|GraphSidecar)"[\s\S]{0,6000}?"(?:display_url|src|url)"\s*:\s*"([^"]+)"'),
            ("image_versions2", r'"image_versions2"[\s\S]{0,3000}?"url"\s*:\s*"([^"]+)"'),
            ("candidates", r'"candidates"\s*:\s*\[[\s\S]{0,3000}?"url"\s*:\s*"([^"]+)"'),
            ("src", r'"src"\s*:\s*"([^"]*(?:cdninstagram|scontent|fbcdn)[^"]*)"'),
            ("json_url", r'"url"\s*:\s*"([^"]*(?:cdninstagram|scontent|fbcdn)[^"]*)"'),
        ):
            for match in re.finditer(pattern, body, flags=re.IGNORECASE):
                candidates.append((source, self._decode_web_image_url(match.group(1))))

        for candidate in self._find_cdn_image_urls(body):
            candidates.append(("cdn_scan", candidate))

        unique: list[tuple[str, str]] = []
        seen: set[str] = set()
        for source, candidate in candidates:
            normalized = self._decode_web_image_url(candidate)
            if not normalized.startswith(("http://", "https://")):
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            unique.append((source, normalized))
        return unique

    def _extract_instagram_metadata_image_candidates(
        self,
        info: object,
    ) -> list[tuple[str, str]]:
        candidates: list[tuple[str, str]] = []
        preferred_keys = {
            "url",
            "webpage_url",
            "thumbnail",
            "display_url",
            "thumbnail_src",
            "src",
        }
        for path, value in self._walk_values(info):
            if not isinstance(value, str):
                continue
            key = path.rsplit(".", 1)[-1]
            decoded = self._decode_web_image_url(value)
            if not decoded.startswith(("http://", "https://")):
                continue
            if key in preferred_keys or self._looks_like_instagram_image_media_url(decoded):
                if self._looks_like_instagram_image_media_url(decoded):
                    candidates.append((f"metadata:{path}", decoded))
        return self._dedupe_candidates(candidates)

    def _walk_values(self, value: object, path: str = "info"):
        if isinstance(value, dict):
            for key, child in value.items():
                yield from self._walk_values(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                yield from self._walk_values(child, f"{path}[{index}]")
        else:
            yield path, value

    def _dedupe_candidates(
        self,
        candidates: list[tuple[str, str]],
    ) -> list[tuple[str, str]]:
        unique: list[tuple[str, str]] = []
        seen: set[str] = set()
        for source, candidate in candidates:
            normalized = self._decode_web_image_url(candidate)
            if not normalized.startswith(("http://", "https://")):
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            unique.append((source, normalized))
        return unique

    def _extract_og_image(self, body: str) -> str | None:
        patterns = (
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        )
        for pattern in patterns:
            match = re.search(pattern, body, flags=re.IGNORECASE)
            if match:
                return self._decode_web_image_url(match.group(1))
        return None

    def _extract_image_from_webpage_json(self, body: str) -> str | None:
        for source, candidate in self._extract_instagram_image_candidates(body):
            if source != "og:image":
                return candidate
        return None

    def _extract_x_image_candidates(self, body: str) -> list[tuple[str, str]]:
        candidates: list[tuple[str, str]] = []

        for source, pattern in (
            (
                "og:image",
                r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            ),
            (
                "og:image",
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            ),
            (
                "twitter:image",
                r'<meta[^>]+(?:name|property)=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)["\']',
            ),
            (
                "twitter:image",
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:name|property)=["\']twitter:image(?::src)?["\']',
            ),
            ("pbs_media", r'"(https?:\\?/\\?/pbs\.twimg\.com/media/[^"]+)"'),
            ("ton_media", r'"(https?:\\?/\\?/ton\.twimg\.com/[^"]+)"'),
            ("json_url", r'"url"\s*:\s*"([^"]*(?:pbs\.twimg\.com/media|ton\.twimg\.com)[^"]*)"'),
        ):
            for match in re.finditer(pattern, body, flags=re.IGNORECASE):
                candidates.append((source, self._decode_web_image_url(match.group(1))))

        for candidate in self._find_x_media_urls(body):
            candidates.append(("media_scan", candidate))

        unique: list[tuple[str, str]] = []
        seen: set[str] = set()
        for source, candidate in candidates:
            normalized = self._decode_web_image_url(candidate)
            if not normalized.startswith(("http://", "https://")):
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            unique.append((source, normalized))
        return unique

    def _decode_web_image_url(self, value: str) -> str:
        decoded = html.unescape(value)
        decoded = decoded.replace("\\u0026", "&")
        decoded = decoded.replace("\\u003d", "=")
        decoded = decoded.replace("\\/", "/")
        try:
            decoded = decoded.encode("utf-8").decode("unicode_escape")
        except UnicodeError:
            pass
        return decoded

    def _find_cdn_image_urls(self, body: str) -> list[str]:
        decoded_body = self._decode_web_image_url(body)
        patterns = (
            r'https?://[^"\'<>\s\\]+(?:cdninstagram\.com|fbcdn\.net|scontent[^"\'<>\s\\]*)[^"\'<>\s\\]+',
            r'https?:\\?/\\?/[^"\'<>\s]+(?:cdninstagram\.com|fbcdn\.net|scontent[^"\'<>\s]*)[^"\'<>\s]+',
        )
        urls: list[str] = []
        for haystack in (body, decoded_body):
            for pattern in patterns:
                for match in re.finditer(pattern, haystack, flags=re.IGNORECASE):
                    candidate = self._decode_web_image_url(match.group(0))
                    if self._looks_like_image_url(candidate, ""):
                        urls.append(candidate)
        return urls

    def _find_x_media_urls(self, body: str) -> list[str]:
        decoded_body = self._decode_web_image_url(body)
        patterns = (
            r'https?://[^"\'<>\s\\]+(?:pbs\.twimg\.com/media|ton\.twimg\.com)[^"\'<>\s\\]+',
            r'https?:\\?/\\?/[^"\'<>\s]+(?:pbs\.twimg\.com/media|ton\.twimg\.com)[^"\'<>\s]+',
        )
        urls: list[str] = []
        for haystack in (body, decoded_body):
            for pattern in patterns:
                for match in re.finditer(pattern, haystack, flags=re.IGNORECASE):
                    candidate = self._decode_web_image_url(match.group(0))
                    if self._looks_like_image_url(candidate, ""):
                        urls.append(candidate)
        return urls

    def _looks_like_instagram_cdn_url(self, url: str) -> bool:
        lower = url.lower()
        return any(host in lower for host in ("cdninstagram.com", "scontent", "fbcdn"))

    def _looks_like_instagram_image_media_url(self, url: str) -> bool:
        lower = url.lower()
        if self._is_instagram_static_asset_url(lower):
            return False
        has_instagram_media_host = any(
            host in lower
            for host in (
                "instagram.f",
                "scontent",
                "fbcdn.net",
                "cdninstagram.com",
            )
        )
        return has_instagram_media_host and self._looks_like_image_url(url, "")

    def _is_instagram_static_asset_url(self, url: str) -> bool:
        lower = url.lower()
        return any(
            marker in lower
            for marker in (
                "static.cdninstagram.com",
                "/rsrc.php",
                "/static/",
                "sprite",
                "icon",
                "logo",
                "favicon",
            )
        )

    def _candidate_priority(self, url: str) -> int:
        lower = url.lower()
        score = 0
        if "scontent" in lower:
            score += 40
        if "cdninstagram.com" in lower and "static.cdninstagram.com" not in lower:
            score += 30
        if "fbcdn.net" in lower:
            score += 25
        if self._looks_like_image_url(url, ""):
            score += 10
        if self._is_instagram_static_asset_url(url):
            score -= 100
        return score

    def _x_candidate_priority(self, url: str) -> int:
        lower = url.lower()
        score = 0
        if "pbs.twimg.com/media" in lower:
            score += 50
        if "ton.twimg.com" in lower:
            score += 30
        if "format=jpg" in lower or "format=png" in lower or "format=webp" in lower:
            score += 15
        if self._looks_like_image_url(url, ""):
            score += 10
        return score

    def _validate_image_candidate(
        self,
        image_url: str,
        referer: str = "https://www.instagram.com/",
    ) -> dict[str, bool | int | str | None]:
        headers = {
            "User-Agent": INSTAGRAM_BROWSER_HEADERS["User-Agent"],
            "Referer": referer,
            "Accept": "image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        }
        if self._is_instagram_static_asset_url(image_url):
            return {"valid": False, "size": None, "reason": "static_asset"}
        try:
            request = Request(image_url, headers=headers)
            with urlopen(request, timeout=20) as response:
                status = int(getattr(response, "status", 200))
                content_type = response.headers.get("content-type", "").lower()
                if status != 200 or not content_type.startswith("image/"):
                    return {"valid": False, "size": None, "reason": "not_image"}

                content_length = response.headers.get("content-length")
                if content_length and content_length.isdigit():
                    size = int(content_length)
                    if size < MIN_INSTAGRAM_POST_IMAGE_BYTES:
                        return {"valid": False, "size": size, "reason": "small_image"}
                    return {"valid": True, "size": size, "reason": "ok"}

                sample = response.read(MIN_INSTAGRAM_POST_IMAGE_BYTES + 1)
                size = len(sample)
                if size < MIN_INSTAGRAM_POST_IMAGE_BYTES:
                    return {"valid": False, "size": size, "reason": "small_image"}
                return {"valid": True, "size": size, "reason": "ok"}
        except Exception as exc:
            logger.info(
                "Instagram image candidate validation failed: %s",
                self._short_error(str(exc)),
            )
            return {"valid": False, "size": None, "reason": "request_failed"}

    def _mask_url(self, url: str) -> str:
        parsed = urlparse(url)
        return urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                "",
                "..." if parsed.query else "",
                "",
            )
        )

    def _log_instagram_image_debug(
        self,
        result: dict[str, bool | int | str | None],
    ) -> None:
        logger.info(
            "Instagram image debug: cookiesEnabled=%s cookieFileExists=%s "
            "htmlStatus=%s finalUrl=%s htmlLength=%s foundOgImage=%s "
            "foundDisplayUrl=%s foundCdnUrlsCount=%s rejectedStaticAssetsCount=%s "
            "rejectedSmallImagesCount=%s acceptedCandidateSize=%s "
            "bestImageUrlMasked=%s reason=%s",
            result.get("cookiesEnabled"),
            result.get("cookieFileExists"),
            result.get("htmlStatus"),
            result.get("finalUrl"),
            result.get("htmlLength"),
            result.get("foundOgImage"),
            result.get("foundDisplayUrl"),
            result.get("foundCdnUrlsCount"),
            result.get("rejectedStaticAssetsCount"),
            result.get("rejectedSmallImagesCount"),
            result.get("acceptedCandidateSize"),
            result.get("bestImageUrlMasked"),
            result.get("reason"),
        )

    def _log_x_image_debug(
        self,
        result: dict[str, bool | int | str | None],
    ) -> None:
        logger.info(
            "X/Twitter image debug: htmlStatus=%s finalUrl=%s htmlLength=%s "
            "foundOgImage=%s foundTwitterImage=%s foundPbsMediaCount=%s "
            "rejectedSmallImagesCount=%s acceptedCandidateSize=%s "
            "bestImageUrlMasked=%s reason=%s",
            result.get("htmlStatus"),
            result.get("finalUrl"),
            result.get("htmlLength"),
            result.get("foundOgImage"),
            result.get("foundTwitterImage"),
            result.get("foundPbsMediaCount"),
            result.get("rejectedSmallImagesCount"),
            result.get("acceptedCandidateSize"),
            result.get("bestImageUrlMasked"),
            result.get("reason"),
        )

    def _cookie_header_from_netscape_file(
        self,
        cookies_file: str | None,
        domain: str = "instagram.com",
    ) -> str | None:
        if not cookies_file:
            return None
        cookie_path = Path(cookies_file)
        if not cookie_path.is_file():
            return None
        cookies: list[str] = []
        try:
            with cookie_path.open("r", encoding="utf-8", errors="ignore") as file:
                for line in file:
                    value = line.strip()
                    if value.startswith("#HttpOnly_"):
                        value = value.removeprefix("#HttpOnly_")
                    if not value or value.startswith("#"):
                        continue
                    parts = value.split("\t")
                    if len(parts) >= 7 and domain in parts[0].lower():
                        cookies.append(f"{parts[5]}={parts[6]}")
        except OSError:
            return None
        return "; ".join(cookies) if cookies else None

    def _is_instagram_image_post_url(self, url: str) -> bool:
        parsed = urlparse(url)
        return "instagram." in (parsed.hostname or "").lower() and parsed.path.startswith("/p/")

    def _instagram_display_id(self, url: str) -> str:
        parts = [part for part in urlparse(url).path.split("/") if part]
        return parts[1] if len(parts) > 1 and parts[0] == "p" else ""

    def _x_status_id(self, url: str) -> str:
        parts = [part for part in urlparse(url).path.split("/") if part]
        if "status" in parts:
            index = parts.index("status")
            if len(parts) > index + 1:
                return parts[index + 1]
        return ""

    def _image_ext_from_url(self, url: str) -> str:
        lower = url.lower()
        if ".png" in lower or "format=png" in lower:
            return "png"
        if ".webp" in lower or "format=webp" in lower:
            return "webp"
        return "jpg"

    def _select_media_info(self, info: dict) -> dict:
        entries = info.get("entries")
        if isinstance(entries, list) and entries:
            first = entries[0]
            if isinstance(first, dict):
                return first
        return info

    def detect_media_type(self, info: dict, platform: str, url: str) -> str:
        lower_url = url.lower()
        if platform == "Instagram" and "/reel/" in lower_url:
            return "video"

        duration = info.get("duration")
        if isinstance(duration, (int, float)) and duration > 0:
            return "video"

        if self._has_video_signals(info):
            return "video"

        if self.extract_best_image_url(info):
            return "image"

        if platform == "Instagram" and "/p/" in lower_url:
            return "image"

        return "video"

    def _has_video_signals(self, info: dict) -> bool:
        for item in self._walk_dicts(info):
            if item.get("requested_downloads"):
                return True
            ext = str(item.get("ext") or "").lower()
            if ext in {"mp4", "webm", "m3u8"} and (
                item.get("url") or item.get("manifest_url")
            ):
                return True
            direct_url = str(item.get("url") or "").lower()
            if any(token in direct_url for token in (".mp4", ".m3u8", ".webm")):
                return True
            vcodec = item.get("vcodec")
            if vcodec not in {None, "none"}:
                return True
        return False

    def _walk_dicts(self, value: object):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from self._walk_dicts(child)
        elif isinstance(value, list):
            for child in value:
                yield from self._walk_dicts(child)

    def extract_best_image_url(self, info: dict) -> str | None:
        image_url, _source = self.extract_best_image_url_with_source(info)
        return image_url

    def extract_best_image_url_with_source(self, info: dict) -> tuple[str | None, str]:
        candidates: list[tuple[int, str, str]] = []
        for item in self._walk_dicts(info):
            url = item.get("url")
            ext = str(item.get("ext") or "").lower()
            if isinstance(url, str) and self._looks_like_image_url(url, ext):
                candidates.append((self._image_score(item, url), url, "direct_url"))
            thumbnail = item.get("thumbnail")
            if isinstance(thumbnail, str) and thumbnail:
                candidates.append((self._image_score(item, thumbnail), thumbnail, "thumbnail"))
            thumbnails = item.get("thumbnails")
            if isinstance(thumbnails, list):
                for thumb in thumbnails:
                    if isinstance(thumb, dict) and isinstance(thumb.get("url"), str):
                        candidates.append(
                            (
                                self._image_score(thumb, str(thumb["url"])),
                                str(thumb["url"]),
                                "thumbnails",
                            )
                        )
        if not candidates:
            return None, "none"
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1], candidates[0][2]

    def _looks_like_image_url(self, url: str, ext: str) -> bool:
        lower = url.lower()
        return ext in {"jpg", "jpeg", "png", "webp"} or any(
            token in lower
            for token in (
                ".jpg",
                ".jpeg",
                ".png",
                ".webp",
                "format=jpg",
                "format=jpeg",
                "format=png",
                "format=webp",
            )
        )

    def _image_score(self, item: dict, url: str) -> int:
        width = item.get("width") if isinstance(item.get("width"), int) else 0
        height = item.get("height") if isinstance(item.get("height"), int) else 0
        preference = 20 if self._looks_like_image_url(url, str(item.get("ext") or "")) else 0
        return width * height + preference

    def _detect_media_type(self, info: dict) -> str:
        return self.detect_media_type(info, "", "")

    def _legacy_detect_media_type(self, info: dict) -> str:
        formats = info.get("formats")
        if isinstance(formats, list):
            for item in formats:
                if not isinstance(item, dict):
                    continue
                if item.get("vcodec") not in {None, "none"}:
                    return "video"
                if item.get("height"):
                    return "video"

        if self._thumbnail(info):
            return "image"

        return "video"

    def _title(self, info: dict) -> str:
        return (
            info.get("title")
            or info.get("description")
            or info.get("display_id")
            or "ApexLoad media"
        )

    def _thumbnail(self, info: dict) -> str:
        thumbnail = info.get("thumbnail")
        if isinstance(thumbnail, str) and thumbnail:
            return thumbnail
        thumbnails = info.get("thumbnails")
        if isinstance(thumbnails, list):
            for item in reversed(thumbnails):
                if isinstance(item, dict) and item.get("url"):
                    return str(item["url"])
        return ""

    def _duration(self, info: dict) -> str | None:
        duration = info.get("duration")
        if not isinstance(duration, (int, float)) or duration <= 0:
            return None
        seconds = int(duration)
        if seconds >= 3600:
            return str(timedelta(seconds=seconds))
        minutes, secs = divmod(seconds, 60)
        return f"{minutes:02d}:{secs:02d}"

    def _video_formats(
        self,
        info: dict,
        thumbnail: str,
        platform: str = "",
    ) -> list[FormatOption]:
        heights = self._available_heights(info)
        duration = info.get("duration")
        snapchat_has_downloadable_video = self._has_video_signals(info) or (
            isinstance(duration, (int, float)) and duration > 0
        )
        if platform == "Snapchat" and not heights and snapchat_has_downloadable_video:
            return [
                FormatOption(
                    id="best",
                    label="MP4 Video",
                    type="video",
                    quality="best",
                    size="Unknown",
                    premium=False,
                    available=True,
                ),
                FormatOption(
                    id="mp3",
                    label="MP3 Audio",
                    type="audio",
                    quality="audio",
                    size="Unknown",
                    premium=True,
                    available=True,
                ),
                FormatOption(
                    id="thumbnail",
                    label="Thumbnail JPG",
                    type="image",
                    quality="thumbnail",
                    size="Unknown" if thumbnail else None,
                    premium=False,
                    available=bool(thumbnail),
                    unavailableReason=None if thumbnail else "Not available on this clip",
                ),
            ]
        return [
            self._video_format("480p", "MP4 480p", 480, False, heights),
            self._video_format("720p", "MP4 720p", 720, False, heights),
            self._video_format("1080p", "MP4 1080p", 1080, True, heights),
            self._video_format("2160p", "MP4 2160p / 4K", 2160, True, heights),
            FormatOption(
                id="mp3",
                label="MP3 Audio",
                type="audio",
                quality="audio",
                size="Unknown",
                premium=True,
                available=True,
            ),
            FormatOption(
                id="thumbnail",
                label="Thumbnail JPG",
                type="image",
                quality="thumbnail",
                size="Unknown" if thumbnail else None,
                premium=False,
                available=bool(thumbnail),
                unavailableReason=None if thumbnail else "Not available on this clip",
            ),
        ]

    def _available_heights(self, info: dict) -> set[int]:
        heights: set[int] = set()
        formats = info.get("formats")
        if not isinstance(formats, list):
            return heights
        for item in formats:
            if not isinstance(item, dict):
                continue
            height = item.get("height")
            ext = str(item.get("ext") or "").lower()
            vcodec = item.get("vcodec")
            if isinstance(height, int) and vcodec not in {None, "none"}:
                if not ext or ext == "mp4":
                    heights.add(height)
        return heights

    def _video_format(
        self,
        format_id: str,
        label: str,
        height: int,
        premium: bool,
        heights: set[int],
    ) -> FormatOption:
        available = any(item >= height for item in heights)
        return FormatOption(
            id=format_id,
            label=label,
            type="video",
            quality=format_id,
            size="Unknown" if available else None,
            premium=premium,
            available=available,
            unavailableReason=None if available else "Not available on this clip",
        )

    def _image_formats(
        self,
        image_url: str | None = None,
        unavailable_reason: str = "No image URL found",
    ) -> list[FormatOption]:
        has_image = bool(image_url)
        if not image_url:
            return [
                FormatOption(
                    id="image",
                    label="Original Image",
                    type="image",
                    quality="original",
                    size=None,
                    premium=False,
                    available=False,
                    unavailableReason=unavailable_reason,
                ),
                FormatOption(
                    id="jpg",
                    label="JPG Image",
                    type="image",
                    quality="jpg",
                    size=None,
                    premium=False,
                    available=False,
                    unavailableReason=unavailable_reason,
                ),
                FormatOption(
                    id="png",
                    label="PNG Image",
                    type="image",
                    quality="png",
                    size=None,
                    premium=True,
                    available=False,
                    unavailableReason=unavailable_reason,
                ),
                FormatOption(
                    id="webp",
                    label="WEBP Image",
                    type="image",
                    quality="webp",
                    size=None,
                    premium=False,
                    available=False,
                    unavailableReason=unavailable_reason,
                ),
                FormatOption(
                    id="high_quality",
                    label="High Quality Image",
                    type="image",
                    quality="high_quality",
                    size=None,
                    premium=True,
                    available=False,
                    unavailableReason=unavailable_reason,
                ),
                FormatOption(
                    id="compressed",
                    label="Compressed Image",
                    type="image",
                    quality="compressed",
                    size=None,
                    premium=False,
                    available=False,
                    unavailableReason=unavailable_reason,
                ),
            ]

        lower_url = image_url.lower()
        is_png = ".png" in lower_url or "format=png" in lower_url
        is_webp = ".webp" in lower_url or "format=webp" in lower_url
        return [
            FormatOption(
                id="original",
                label="Original Image",
                type="image",
                quality="original",
                size="Unknown" if has_image else None,
                premium=False,
                available=has_image,
                unavailableReason=None if has_image else "Not available for this image",
            ),
            FormatOption(
                id="jpg",
                label="JPG Image",
                type="image",
                quality="jpg",
                size="Unknown" if has_image else None,
                premium=False,
                available=has_image,
                unavailableReason=None if has_image else "Not available for this image",
            ),
            FormatOption(
                id="png",
                label="PNG Image",
                type="image",
                quality="png",
                size="Unknown" if is_png else None,
                premium=True,
                available=is_png,
                unavailableReason=None if is_png else "Not available for this image",
            ),
            FormatOption(
                id="webp",
                label="WEBP Image",
                type="image",
                quality="webp",
                size="Unknown" if is_webp else None,
                premium=False,
                available=is_webp,
                unavailableReason=None if is_webp else "Not available for this image",
            ),
            FormatOption(
                id="high_quality",
                label="High Quality Image",
                type="image",
                quality="high_quality",
                size="Unknown" if has_image else None,
                premium=True,
                available=has_image,
                unavailableReason=None if has_image else "Not available for this image",
            ),
            FormatOption(
                id="compressed",
                label="Compressed Image",
                type="image",
                quality="compressed",
                size="Unknown" if has_image else None,
                premium=False,
                available=has_image,
                unavailableReason=None if has_image else "Not available for this image",
            ),
        ]
