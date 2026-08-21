from __future__ import annotations

import html
import ipaddress
import json
import logging
import re
import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlencode, urljoin, urlparse

from app.services.tiktok_recovery import (
    TikTokOperationCancelled,
    sanitized_tiktok_video_id,
)
from app.services.ytdlp_options import TIKTOK_CHROME_USER_AGENT
from app.utils.platform_detector import detect_platform

logger = logging.getLogger("apexload.tiktok_embed")

TIKTOK_EMBED_PLAYER_TEMPLATE = "https://www.tiktok.com/player/v1/{video_id}"
TIKTOK_EMBED_METADATA_ENDPOINT = "https://www.tiktok.com/player/api/v1/items"
TIKTOK_VIDEO_ID_PATTERN = re.compile(r"^[0-9]{15,22}$")
TIKTOK_CDN_HOST_SUFFIXES = (
    "tiktok.com",
    "tiktokcdn.com",
    "tiktokcdn-us.com",
    "tiktokcdn-eu.com",
    "tiktokv.com",
    "byteoversea.com",
    "byteoversea.net",
    "ibytedtos.com",
    "muscdn.com",
)
MAX_EMBED_BYTES = 5 * 1024 * 1024
MAX_MEDIA_REDIRECTS = 5

_MEDIA_KEYS = {
    "playaddr",
    "downloadaddr",
    "playurl",
    "play_url",
    "play_addr",
    "download_addr",
    "video_url",
    "videourl",
}
_MEDIA_LIST_KEYS = {"play_addr_list", "playurllist"}
_THUMBNAIL_KEYS = {
    "cover",
    "origincover",
    "origin_cover",
    "dynamiccover",
    "dynamic_cover",
    "poster",
    "thumbnail",
    "thumbnail_url",
}
_TITLE_KEYS = {"desc", "description", "title"}


class TikTokEmbedError(RuntimeError):
    pass


class TikTokEmbedSecurityError(TikTokEmbedError):
    pass


@dataclass(frozen=True)
class TikTokEmbedMedia:
    video_id: str
    media_url: str
    media_host: str
    title: str | None = None
    thumbnail: str | None = None
    width: int | None = None
    height: int | None = None

    def as_ytdlp_info(self) -> dict[str, Any]:
        media_format: dict[str, Any] = {
            "format_id": "tiktok_embed",
            "url": self.media_url,
            "ext": "mp4",
            "vcodec": "unknown",
            "acodec": "unknown",
        }
        if self.width:
            media_format["width"] = self.width
        if self.height:
            media_format["height"] = self.height
        result: dict[str, Any] = {
            "id": self.video_id,
            "display_id": self.video_id,
            "title": self.title or f"TikTok video {self.video_id}",
            "description": self.title or "",
            "thumbnail": self.thumbnail or "",
            "url": self.media_url,
            "ext": "mp4",
            "vcodec": "unknown",
            "acodec": "unknown",
            "formats": [media_format],
            "extractor": "tiktok_embed",
            "extractor_key": "TikTokEmbed",
        }
        if self.width:
            result["width"] = self.width
        if self.height:
            result["height"] = self.height
        return result


class TikTokEmbedService:
    def __init__(
        self,
        requester: Callable[..., Any] | None = None,
        resolver: Callable[..., Any] | None = None,
    ) -> None:
        self._requester = requester
        self._resolver = resolver or socket.getaddrinfo

    def resolve(self, url: str) -> TikTokEmbedMedia:
        video_id = parse_tiktok_video_id(url)
        embed_url = TIKTOK_EMBED_PLAYER_TEMPLATE.format(video_id=video_id)
        try:
            body = self._fetch_embed_page(embed_url, video_id)
            media = parse_tiktok_embed_html(
                body,
                video_id,
                resolver=self._resolver,
            )
        except TikTokEmbedSecurityError:
            raise
        except TikTokEmbedError:
            metadata = self._fetch_embed_metadata(video_id, embed_url)
            media = parse_tiktok_embed_data(
                metadata,
                video_id,
                resolver=self._resolver,
            )
        logger.info(
            "TikTok embed fallback. video_id=%s fallback_used=%s result=%s "
            "media_host=%s",
            video_id,
            True,
            "resolved",
            media.media_host,
        )
        return media

    def download(
        self,
        media: TikTokEmbedMedia,
        target: Path,
        *,
        progress_callback: Callable[[int, int | None], None] | None = None,
        cancellation_check: Callable[[], bool] | None = None,
    ) -> Path:
        if target.is_file() and target.stat().st_size > 0:
            return target
        part_path = target.with_name(f"{target.name}.part")
        current_url = media.media_url
        try:
            for redirect_number in range(MAX_MEDIA_REDIRECTS + 1):
                validate_tiktok_media_url(current_url, resolver=self._resolver)
                response = self._request(
                    current_url,
                    stream=True,
                    allow_redirects=False,
                )
                status = int(getattr(response, "status_code", 0) or 0)
                if status in {301, 302, 303, 307, 308}:
                    location = _response_header(response, "location")
                    _close_response(response)
                    if not location or redirect_number >= MAX_MEDIA_REDIRECTS:
                        raise TikTokEmbedSecurityError(
                            "TikTok media redirect could not be validated."
                        )
                    current_url = urljoin(current_url, location)
                    continue
                if status != 200:
                    _close_response(response)
                    raise TikTokEmbedError("TikTok public media download failed.")

                content_type = _response_header(response, "content-type").lower()
                if content_type and not (
                    content_type.startswith("video/")
                    or content_type.startswith("audio/")
                    or content_type.startswith("application/octet-stream")
                ):
                    _close_response(response)
                    raise TikTokEmbedSecurityError(
                        "TikTok public media response was not media content."
                    )

                total = _positive_int(_response_header(response, "content-length"))
                target.parent.mkdir(parents=True, exist_ok=True)
                downloaded = 0
                try:
                    with part_path.open("wb") as output:
                        for chunk in response.iter_content(chunk_size=256 * 1024):
                            if cancellation_check and cancellation_check():
                                raise TikTokOperationCancelled(
                                    "TikTok embed download cancelled."
                                )
                            if not chunk:
                                continue
                            output.write(chunk)
                            downloaded += len(chunk)
                            if progress_callback:
                                progress_callback(downloaded, total)
                finally:
                    _close_response(response)
                if downloaded <= 0:
                    raise TikTokEmbedError("TikTok public media response was empty.")
                part_path.replace(target)
                logger.info(
                    "TikTok embed media download. video_id=%s fallback_used=%s "
                    "result=%s media_host=%s",
                    media.video_id,
                    True,
                    "succeeded",
                    urlparse(current_url).hostname or "unknown",
                )
                return target
            raise TikTokEmbedSecurityError("Too many TikTok media redirects.")
        except Exception:
            try:
                if part_path.exists():
                    part_path.unlink()
            except OSError:
                pass
            raise

    def _fetch_embed_page(self, url: str, video_id: str) -> str:
        current_url = url
        for redirect_number in range(MAX_MEDIA_REDIRECTS + 1):
            validate_tiktok_embed_url(
                current_url,
                expected_video_id=video_id,
                resolver=self._resolver,
            )
            response = self._request(current_url, allow_redirects=False)
            status = int(getattr(response, "status_code", 0) or 0)
            if status in {301, 302, 303, 307, 308}:
                location = _response_header(response, "location")
                _close_response(response)
                if not location or redirect_number >= MAX_MEDIA_REDIRECTS:
                    raise TikTokEmbedSecurityError(
                        "TikTok embed redirect could not be validated."
                    )
                current_url = urljoin(current_url, location)
                continue
            if status != 200:
                _close_response(response)
                raise TikTokEmbedError("TikTok public embed page was unavailable.")
            body = str(getattr(response, "text", "") or "")
            _close_response(response)
            if not body or len(body.encode("utf-8")) > MAX_EMBED_BYTES:
                raise TikTokEmbedError("TikTok public embed data was invalid.")
            return body
        raise TikTokEmbedSecurityError("Too many TikTok embed redirects.")

    def _fetch_embed_metadata(self, video_id: str, referer: str) -> dict[str, Any]:
        query = urlencode(
            {
                "item_ids": video_id,
                "language": "en",
                "aid": "1459",
                "data_source": "web_core",
            }
        )
        metadata_url = f"{TIKTOK_EMBED_METADATA_ENDPOINT}?{query}"
        response = self._request(
            metadata_url,
            allow_redirects=False,
            referer=referer,
        )
        status = int(getattr(response, "status_code", 0) or 0)
        body = str(getattr(response, "text", "") or "")
        _close_response(response)
        if status != 200 or not body or len(body.encode("utf-8")) > MAX_EMBED_BYTES:
            raise TikTokEmbedError("TikTok public embed metadata was unavailable.")
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, TypeError) as exc:
            raise TikTokEmbedError("TikTok public embed metadata was invalid.") from exc
        if not isinstance(payload, dict):
            raise TikTokEmbedError("TikTok public embed metadata was invalid.")
        results = payload.get("results")
        if isinstance(results, list) and not any(
            isinstance(item, dict)
            and str(item.get("id_str") or item.get("id") or "") == video_id
            and item.get("code") == "ok"
            for item in results
        ):
            raise TikTokEmbedError("TikTok public video was not available to embed.")
        items = payload.get("items")
        if not isinstance(items, list):
            raise TikTokEmbedError("TikTok public embed metadata had no video item.")
        matching_items = [
            item
            for item in items
            if isinstance(item, dict)
            and str(item.get("id_str") or item.get("id") or "") == video_id
        ]
        if not matching_items:
            raise TikTokEmbedError("TikTok public embed metadata did not match the video.")
        return {"items": matching_items}

    def _request(self, url: str, **kwargs: Any):
        requester = self._requester
        if requester is None:
            from curl_cffi import requests

            requester = requests.get
        from curl_cffi.const import CurlOpt

        parsed = urlparse(url)
        host = parsed.hostname or ""
        addresses = _public_dns_addresses(host, self._resolver)
        pinned_addresses = [
            f"{host}:443:{f'[{address}]' if ':' in address else address}"
            for address in addresses
        ]
        referer = str(kwargs.pop("referer", "https://www.tiktok.com/") or "")
        headers = {
            "User-Agent": TIKTOK_CHROME_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,video/mp4,video/*;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": referer,
        }
        return requester(
            url,
            headers=headers,
            impersonate="chrome",
            timeout=30,
            proxy="",
            curl_options={CurlOpt.RESOLVE: pinned_addresses},
            **kwargs,
        )


def parse_tiktok_video_id(url: str) -> str:
    if detect_platform(url) != "TikTok":
        raise TikTokEmbedError("URL is not a supported TikTok link.")
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise TikTokEmbedError("TikTok link has an invalid network location.") from exc
    if (
        parsed.scheme.lower() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        raise TikTokEmbedError("TikTok link must be a public HTTPS video URL.")
    public_video_match = re.fullmatch(
        r"/(?:@[^/]*/video|share/video)/[0-9]{15,22}/?",
        parsed.path,
    )
    embed_match = re.fullmatch(
        r"/(?:embed|player/v1)/([0-9]{15,22})/?",
        parsed.path,
    )
    if public_video_match:
        video_id = sanitized_tiktok_video_id(url)
    elif embed_match:
        video_id = embed_match.group(1)
    else:
        video_id = ""
    if not TIKTOK_VIDEO_ID_PATTERN.fullmatch(video_id):
        raise TikTokEmbedError("TikTok link does not contain a valid public video ID.")
    return video_id


def supports_tiktok_embed_fallback(url: str) -> bool:
    try:
        parse_tiktok_video_id(url)
    except TikTokEmbedError:
        return False
    return True


def parse_tiktok_embed_html(
    body: str,
    video_id: str,
    *,
    resolver: Callable[..., Any] | None = None,
) -> TikTokEmbedMedia:
    if not TIKTOK_VIDEO_ID_PATTERN.fullmatch(video_id):
        raise TikTokEmbedError("TikTok embed video ID is invalid.")
    documents = list(_json_documents(body))
    return _parse_tiktok_embed_documents(
        documents,
        body,
        video_id,
        resolver=resolver,
    )


def parse_tiktok_embed_data(
    data: dict[str, Any],
    video_id: str,
    *,
    resolver: Callable[..., Any] | None = None,
) -> TikTokEmbedMedia:
    if not isinstance(data, dict):
        raise TikTokEmbedError("TikTok public embed metadata was invalid.")
    return _parse_tiktok_embed_documents(
        [data],
        "",
        video_id,
        resolver=resolver,
    )


def _parse_tiktok_embed_documents(
    documents: list[Any],
    body: str,
    video_id: str,
    *,
    resolver: Callable[..., Any] | None,
) -> TikTokEmbedMedia:
    if not TIKTOK_VIDEO_ID_PATTERN.fullmatch(video_id):
        raise TikTokEmbedError("TikTok embed video ID is invalid.")
    media_candidates: list[str] = []
    thumbnail_candidates: list[str] = []
    titles: list[str] = []
    dimensions: list[tuple[int | None, int | None]] = []
    for document in documents:
        _collect_embed_values(
            document,
            media_candidates,
            thumbnail_candidates,
            titles,
            dimensions,
        )
    media_candidates.extend(_html_media_candidates(body))
    thumbnail_candidates.extend(_html_meta_candidates(body, "image"))
    titles.extend(_html_title_candidates(body))

    public_resolver = resolver or socket.getaddrinfo
    media_url = ""
    media_host = ""
    for candidate in _dedupe(media_candidates):
        normalized = _normalize_candidate_url(candidate)
        try:
            media_host = validate_tiktok_media_url(
                normalized,
                resolver=public_resolver,
            )
        except TikTokEmbedSecurityError:
            continue
        media_url = normalized
        break
    if not media_url:
        raise TikTokEmbedError("TikTok public embed did not expose downloadable media.")

    thumbnail = None
    for candidate in _dedupe(thumbnail_candidates):
        normalized = _normalize_candidate_url(candidate)
        try:
            validate_tiktok_media_url(normalized, resolver=public_resolver)
        except TikTokEmbedSecurityError:
            continue
        thumbnail = normalized
        break
    width, height = next(
        ((width, height) for width, height in dimensions if width or height),
        (None, None),
    )
    title = next((value.strip() for value in titles if value.strip()), None)
    return TikTokEmbedMedia(
        video_id=video_id,
        media_url=media_url,
        media_host=media_host,
        title=title[:500] if title else None,
        thumbnail=thumbnail,
        width=width,
        height=height,
    )


def validate_tiktok_embed_url(
    url: str,
    *,
    expected_video_id: str | None = None,
    resolver: Callable[..., Any] | None = None,
) -> str:
    host = _validate_https_url(
        url,
        allowed=lambda host: host in {"www.tiktok.com", "tiktok.com"},
        resolver=resolver or socket.getaddrinfo,
    )
    if expected_video_id is not None:
        parsed = urlparse(url)
        if parsed.path.rstrip("/") != f"/player/v1/{expected_video_id}":
            raise TikTokEmbedSecurityError(
                "TikTok embed redirect changed the requested video."
            )
    return host


def validate_tiktok_media_url(
    url: str,
    *,
    resolver: Callable[..., Any] | None = None,
) -> str:
    return _validate_https_url(
        url,
        allowed=is_allowed_tiktok_cdn_host,
        resolver=resolver or socket.getaddrinfo,
    )


def is_allowed_tiktok_cdn_host(host: str) -> bool:
    normalized = host.rstrip(".").lower()
    return any(
        normalized == suffix or normalized.endswith(f".{suffix}")
        for suffix in TIKTOK_CDN_HOST_SUFFIXES
    )


def _validate_https_url(
    url: str,
    *,
    allowed: Callable[[str], bool],
    resolver: Callable[..., Any],
) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").rstrip(".").lower()
    if (
        parsed.scheme.lower() != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
        or not allowed(host)
    ):
        raise TikTokEmbedSecurityError("TikTok URL failed the HTTPS host allowlist.")
    _require_public_dns(host, resolver)
    return host


def _require_public_dns(host: str, resolver: Callable[..., Any]) -> None:
    _public_dns_addresses(host, resolver)


def _public_dns_addresses(host: str, resolver: Callable[..., Any]) -> list[str]:
    try:
        results = resolver(host, 443, type=socket.SOCK_STREAM)
    except (OSError, TypeError) as exc:
        raise TikTokEmbedSecurityError("TikTok media host could not be resolved.") from exc
    addresses = {
        str(item[4][0])
        for item in results
        if isinstance(item, tuple)
        and len(item) >= 5
        and isinstance(item[4], tuple)
        and item[4]
    }
    if not addresses:
        raise TikTokEmbedSecurityError("TikTok media host had no usable address.")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address.split("%", 1)[0])
        except ValueError as exc:
            raise TikTokEmbedSecurityError("TikTok media host resolved unsafely.") from exc
        if not ip.is_global:
            raise TikTokEmbedSecurityError(
                "TikTok media host resolved to a non-public address."
            )
    return sorted(addresses)


def _json_documents(body: str) -> Iterable[Any]:
    for match in re.finditer(r"<script\b[^>]*>(.*?)</script>", body, re.I | re.S):
        payload = html.unescape(match.group(1).strip())
        if not payload or payload[0] not in "[{":
            continue
        try:
            yield json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            continue


def _collect_embed_values(
    value: Any,
    media: list[str],
    thumbnails: list[str],
    titles: list[str],
    dimensions: list[tuple[int | None, int | None]],
) -> None:
    if isinstance(value, dict):
        width = _positive_int(value.get("width"))
        height = _positive_int(value.get("height"))
        if width or height:
            dimensions.append((width, height))
        for key, item in value.items():
            normalized_key = str(key).replace("-", "").lower()
            if normalized_key in _MEDIA_KEYS:
                media.extend(_string_values(item))
            elif normalized_key in _MEDIA_LIST_KEYS:
                media.extend(_string_values(item))
            elif normalized_key in _THUMBNAIL_KEYS:
                thumbnails.extend(_string_values(item))
            elif normalized_key in _TITLE_KEYS:
                titles.extend(_string_values(item))
            _collect_embed_values(item, media, thumbnails, titles, dimensions)
    elif isinstance(value, list):
        for item in value:
            _collect_embed_values(item, media, thumbnails, titles, dimensions)


def _string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    if isinstance(value, dict):
        values: list[str] = []
        for key in ("url", "src", "urlList", "url_list"):
            if key in value:
                values.extend(_string_values(value[key]))
        return values
    return []


def _html_media_candidates(body: str) -> list[str]:
    candidates = _html_meta_candidates(body, "video")
    candidates.extend(
        html.unescape(value)
        for value in re.findall(
            r"<(?:video|source)\b[^>]*\bsrc=[\"']([^\"']+)",
            body,
            re.I,
        )
    )
    return candidates


def _html_meta_candidates(body: str, kind: str) -> list[str]:
    pattern = (
        rf"<meta\b[^>]*(?:property|name)=[\"'](?:og:|twitter:){kind}(?::url)?[\"']"
        r"[^>]*content=[\"']([^\"']+)"
    )
    return [html.unescape(value) for value in re.findall(pattern, body, re.I)]


def _html_title_candidates(body: str) -> list[str]:
    values = _html_meta_candidates(body, "description")
    values.extend(
        html.unescape(value)
        for value in re.findall(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
    )
    return values


def _normalize_candidate_url(value: str) -> str:
    normalized = html.unescape(value).replace("\\u002F", "/").replace("\\/", "/")
    normalized = unquote(normalized.strip()) if normalized.startswith("https%3A") else normalized.strip()
    return normalized


def _dedupe(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if isinstance(value, str) and value))


def _response_header(response: Any, name: str) -> str:
    headers = getattr(response, "headers", {}) or {}
    return str(headers.get(name) or headers.get(name.title()) or "")


def _close_response(response: Any) -> None:
    close = getattr(response, "close", None)
    if callable(close):
        close()


def _positive_int(value: object) -> int | None:
    try:
        number = int(str(value))
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number > 0 else None


tiktok_embed_service = TikTokEmbedService()
