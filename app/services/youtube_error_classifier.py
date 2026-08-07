from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class YouTubeErrorCode(StrEnum):
    ANTI_BOT = "YOUTUBE_ANTI_BOT"
    RATE_LIMITED = "YOUTUBE_RATE_LIMITED"
    PROXY_UNAVAILABLE = "YOUTUBE_PROXY_UNAVAILABLE"
    CONTENT_UNAVAILABLE = "YOUTUBE_CONTENT_UNAVAILABLE"
    PRIVATE = "YOUTUBE_PRIVATE"
    AGE_OR_AUTH_REQUIRED = "YOUTUBE_AGE_OR_AUTH_REQUIRED"
    FORMAT_UNAVAILABLE = "YOUTUBE_FORMAT_UNAVAILABLE"
    TEMPORARY_UNAVAILABLE = "YOUTUBE_TEMPORARY_UNAVAILABLE"
    DOWNLOAD_FAILED = "YOUTUBE_DOWNLOAD_FAILED"
    NETWORK_ERROR = "NETWORK_ERROR"


@dataclass(frozen=True, slots=True)
class YouTubeErrorClassification:
    code: YouTubeErrorCode
    user_message: str
    proxy_related: bool = False
    retryable: bool = False
    verify_with_another_proxy: bool = False


MESSAGES = {
    YouTubeErrorCode.ANTI_BOT: "YouTube is temporarily limiting this request. Please try again shortly.",
    YouTubeErrorCode.RATE_LIMITED: "YouTube is temporarily limiting this request. Please try again shortly.",
    YouTubeErrorCode.PROXY_UNAVAILABLE: "YouTube is temporarily unavailable. Please try again shortly.",
    YouTubeErrorCode.CONTENT_UNAVAILABLE: "This YouTube video is unavailable, private, or has been removed.",
    YouTubeErrorCode.PRIVATE: "This YouTube video is private.",
    YouTubeErrorCode.AGE_OR_AUTH_REQUIRED: "This YouTube video requires age verification or authentication.",
    YouTubeErrorCode.FORMAT_UNAVAILABLE: "This YouTube format is not available. Try another quality.",
    YouTubeErrorCode.TEMPORARY_UNAVAILABLE: "YouTube is temporarily unavailable. Please try again shortly.",
    YouTubeErrorCode.DOWNLOAD_FAILED: "The YouTube download could not be completed. Please try again.",
    YouTubeErrorCode.NETWORK_ERROR: "A network error interrupted the request. Please try again.",
}


def classify_youtube_error(error: BaseException | str) -> YouTubeErrorClassification:
    text = str(error).lower()

    if any(marker in text for marker in ("private video", "this video is private", "members-only")):
        return _result(YouTubeErrorCode.PRIVATE)
    if any(marker in text for marker in (
        "confirm your age", "age-restricted", "age restricted", "inappropriate for some users",
        "login required to view", "authentication required",
    )):
        return _result(YouTubeErrorCode.AGE_OR_AUTH_REQUIRED)
    if any(marker in text for marker in (
        "video unavailable", "this video is unavailable", "has been removed",
        "video has been removed", "account associated with this video has been terminated",
    )):
        return _result(YouTubeErrorCode.CONTENT_UNAVAILABLE, verify_with_another_proxy=True)
    if any(marker in text for marker in (
        "requested format is not available", "format is not available", "requested format not available",
    )):
        return _result(YouTubeErrorCode.FORMAT_UNAVAILABLE)
    if any(marker in text for marker in (
        "sign in to confirm you're not a bot", "sign in to confirm you’re not a bot",
        "confirm you're not a bot", "confirm you’re not a bot", "login_required", "not a bot",
    )):
        return _result(YouTubeErrorCode.ANTI_BOT, proxy_related=True, retryable=True)
    if any(marker in text for marker in (
        "http error 429", "too many requests", "rate limit", "rate-limit", "resource exhausted",
    )):
        return _result(YouTubeErrorCode.RATE_LIMITED, proxy_related=True, retryable=True)
    if any(marker in text for marker in (
        "proxy error", "connection refused", "connection reset", "connection aborted",
        "proxy timeout", "timed out", "timeout", "temporary failure in name resolution",
        "name or service not known", "network is unreachable", "remote end closed connection",
        "http error 403", "fragment", "unable to download video data", "missing media url",
    )):
        return _result(YouTubeErrorCode.NETWORK_ERROR, proxy_related=True, retryable=True)
    if any(marker in text for marker in (
        "no video formats found", "only images are available", "challenge solving failed",
        "the page needs to be reloaded", "temporarily unavailable", "try again later",
    )):
        return _result(YouTubeErrorCode.TEMPORARY_UNAVAILABLE, proxy_related=True, retryable=True)
    return _result(YouTubeErrorCode.DOWNLOAD_FAILED)


def _result(
    code: YouTubeErrorCode,
    *,
    proxy_related: bool = False,
    retryable: bool = False,
    verify_with_another_proxy: bool = False,
) -> YouTubeErrorClassification:
    return YouTubeErrorClassification(
        code=code,
        user_message=MESSAGES[code],
        proxy_related=proxy_related,
        retryable=retryable,
        verify_with_another_proxy=verify_with_another_proxy,
    )


class YouTubeOperationError(RuntimeError):
    def __init__(self, classification: YouTubeErrorClassification, technical_message: str = "") -> None:
        self.classification = classification
        self.technical_message = technical_message
        super().__init__(classification.user_message)

