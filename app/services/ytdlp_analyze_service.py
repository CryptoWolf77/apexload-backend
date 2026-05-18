import ipaddress
import logging
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app.core.config import get_settings
from app.models.analyze_models import AnalyzeResponse, FormatOption
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
    message = "Instagram blocked this request. Try another public link or try again later."


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
        else:
            logger.info("yt-dlp analyze started")
            info = self._extract_info(normalized_url)
            source = "yt_dlp"
        media_info = self._select_media_info(info)
        media_type = self._detect_media_type(media_info)
        logger.info("yt-dlp analyze success. mediaType=%s", media_type)

        thumbnail = self._thumbnail(media_info)
        response = AnalyzeResponse(
            success=True,
            source=source,
            platform=platform,
            mediaType=media_type,
            title=self._title(media_info),
            thumbnail=thumbnail,
            duration=self._duration(media_info) if media_type == "video" else None,
            formats=(
                self._video_formats(media_info, thumbnail)
                if media_type == "video"
                else self._image_formats(thumbnail)
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
        settings = get_settings()
        if not settings.enable_instagram_cookies:
            logger.info("Instagram cookies disabled. Skipping cookie retry.")
            return None
        if not settings.instagram_cookies_file:
            logger.info("Instagram cookies enabled but cookie file not found: (empty)")
            return None

        cookie_path = Path(settings.instagram_cookies_file).expanduser().resolve()
        if not cookie_path.is_file():
            logger.info(
                "Instagram cookies enabled but cookie file not found: %s",
                cookie_path,
            )
            return None
        if not self._cookie_file_looks_valid(cookie_path):
            logger.info(
                "Instagram cookies enabled but cookie file is not valid: %s",
                cookie_path,
            )
            return None
        return str(cookie_path)

    def instagram_cookie_status(self) -> dict[str, bool | int | str]:
        settings = get_settings()
        raw_path = settings.instagram_cookies_file
        resolved_path = str(Path(raw_path).expanduser().resolve()) if raw_path else ""
        cookie_path = Path(resolved_path) if resolved_path else None
        exists = bool(cookie_path and cookie_path.is_file())
        size = cookie_path.stat().st_size if cookie_path and exists else 0
        valid = bool(cookie_path and self._cookie_file_looks_valid(cookie_path))
        return {
            "enableInstagramCookies": settings.enable_instagram_cookies,
            "cookieFileRaw": raw_path,
            "cookieFileResolved": resolved_path,
            "cookieFileExists": exists,
            "cookieFileSize": size,
            "cookieFileValid": valid,
        }

    def _log_instagram_cookie_settings(self) -> None:
        settings = get_settings()
        cookie_path = (
            Path(settings.instagram_cookies_file).expanduser().resolve()
            if settings.instagram_cookies_file
            else None
        )
        logger.info(
            "ENABLE_INSTAGRAM_COOKIES=%s",
            settings.enable_instagram_cookies,
        )
        logger.info(
            "INSTAGRAM_COOKIES_FILE=%s",
            settings.instagram_cookies_file,
        )
        logger.info(
            "Instagram cookie file resolved path=%s",
            str(cookie_path) if cookie_path else "",
        )
        logger.info(
            "Instagram cookie file exists=%s",
            bool(cookie_path and cookie_path.is_file()),
        )
        logger.info(
            "Instagram cookie file valid=%s",
            bool(cookie_path and self._cookie_file_looks_valid(cookie_path)),
        )

    def _cookie_file_looks_valid(self, cookie_path: Path) -> bool:
        if not cookie_path.is_file() or cookie_path.stat().st_size <= 0:
            return False
        try:
            with cookie_path.open("r", encoding="utf-8", errors="ignore") as file:
                for line in file:
                    normalized = line.strip().lower()
                    if not normalized or normalized.startswith("#"):
                        continue
                    if "instagram.com" in normalized and len(line.split("\t")) >= 7:
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
        )
        return any(marker in text for marker in blocked_markers)

    def _short_error(self, message: str) -> str:
        compact = " ".join(message.split())
        return compact[:240]

    def _log_backend_instagram_debug(self) -> None:
        status = self.instagram_cookie_status()
        logger.info("BACKEND INSTAGRAM DEBUG:")
        logger.info(
            "ENABLE_INSTAGRAM_COOKIES=%s",
            status["enableInstagramCookies"],
        )
        logger.info("INSTAGRAM_COOKIES_FILE=%s", status["cookieFileRaw"])
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
        cookiefile = self._instagram_cookiefile()
        if not cookiefile:
            raise InstagramAuthRequiredError()
        return self._extract_instagram_info_with_direct_cookies(clean_url, cookiefile)

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
        self._log_instagram_cookie_settings()
        cookiefile = self._instagram_cookiefile()
        if not cookiefile:
            raise InstagramAuthRequiredError(
                raw_message=last_error.raw_message
            ) from last_error

        attempts = (
            (
                "Instagram attempt 2: cookies started",
                "yt_dlp_cookies",
                "direct_cookies",
            ),
            (
                "Instagram attempt 3: cookies + app_id started",
                "yt_dlp_cookies_app_id",
                {"extractor_args": {"instagram": {"app_id": [INSTAGRAM_WEB_APP_ID]}}},
            ),
            (
                "Instagram attempt 4: cookies + app_id + headers started",
                "yt_dlp_cookies_app_id_headers",
                {
                    "extractor_args": {
                        "instagram": {"app_id": [INSTAGRAM_WEB_APP_ID]}
                    },
                    "http_headers": INSTAGRAM_BROWSER_HEADERS,
                },
            ),
        )

        for index, (log_message, source, extra_options) in enumerate(attempts, start=2):
            logger.info(log_message)
            try:
                if extra_options == "direct_cookies":
                    info = self._extract_instagram_info_with_direct_cookies(
                        url,
                        cookiefile,
                    )
                else:
                    info = self._extract_info(
                        url,
                        cookiefile=cookiefile,
                        extra_options=extra_options,
                    )
                logger.info("Instagram attempt %s succeeded", index)
                return info, source
            except AnalyzeServiceError as exc:
                logger.info(
                    "Instagram attempt %s failed: %s",
                    index,
                    self._short_error(exc.raw_message),
                )
                last_error = exc

        raise InstagramAuthRequiredError(
            raw_message=last_error.raw_message
        ) from last_error

    def _extract_info(
        self,
        url: str,
        cookiefile: str | None = None,
        extra_options: dict | None = None,
    ) -> dict:
        try:
            import yt_dlp

            options = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "download": False,
                "noplaylist": True,
                "socket_timeout": 15,
                "extract_flat": False,
            }
            if cookiefile:
                options["cookiefile"] = cookiefile
            if extra_options:
                options.update(extra_options)
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
        except UnsupportedUrlError:
            raise
        except Exception as exc:
            raw_message = str(exc)
            logger.warning("yt-dlp analyze failure: %s", raw_message)
            raise AnalyzeServiceError(raw_message=raw_message) from exc

        if not isinstance(info, dict):
            raise AnalyzeServiceError()
        return info

    def _select_media_info(self, info: dict) -> dict:
        entries = info.get("entries")
        if isinstance(entries, list) and entries:
            first = entries[0]
            if isinstance(first, dict):
                return first
        return info

    def _detect_media_type(self, info: dict) -> str:
        duration = info.get("duration")
        if isinstance(duration, (int, float)) and duration > 0:
            return "video"

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

        # TODO: Improve media type detection for galleries/carousels after more
        # real platform responses are observed. Defaulting to video preserves
        # frontend compatibility for uncertain URLs.
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

    def _video_formats(self, info: dict, thumbnail: str) -> list[FormatOption]:
        heights = self._available_heights(info)
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

    def _image_formats(self, image_url: str) -> list[FormatOption]:
        has_image = bool(image_url)
        is_png = image_url.lower().split("?")[0].endswith(".png")
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
