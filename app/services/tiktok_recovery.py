import logging
import random
import re
import time
from collections.abc import Callable
from typing import Any, TypeVar

from app.services.ytdlp_options import (
    build_supported_impersonate_target,
    build_ytdlp_options,
)

logger = logging.getLogger("apexload.tiktok_recovery")

T = TypeVar("T")

TIKTOK_MAX_ATTEMPTS = 3
TIKTOK_TEMPORARY_MESSAGE = (
    "TikTok temporarily rejected this request. Please try again shortly or use "
    "another public TikTok link."
)
TIKTOK_UNAVAILABLE_MESSAGE = (
    "This TikTok video is private, unavailable, or requires login."
)

_PERMANENT_MARKERS = (
    "private video",
    "video is private",
    "this video is private",
    "has been deleted",
    "video has been removed",
    "video is unavailable",
    "video not available",
    "video is no longer available",
    "this video is no longer available",
    "content is unavailable",
    "requested content is not available",
    "this content is not available",
    "account is private",
    "login required",
    "log in to view",
    "sign in to view",
    "unsupported url",
    "unsupported link",
    "geo-restricted",
    "geo restricted",
    "geo blocked",
    "geoblocked",
    "not available in your country",
    "not available in your region",
    "invalid media id",
    "invalid video id",
)
_CANCELLATION_MARKERS = (
    "cancelled by user",
    "canceled by user",
    "user cancelled",
    "user canceled",
    "operation cancelled",
    "operation canceled",
)
_TRANSIENT_MARKERS = (
    ("unexpected response from webpage request", "unexpected_webpage_response"),
    (
        "unable to extract universal data for rehydration",
        "missing_rehydration_data",
    ),
    ("challenge-response", "challenge_response"),
    ("challenge response", "challenge_response"),
    ("webpage challenge", "challenge_response"),
    ("tiktok challenge", "challenge_response"),
    ("http error 403", "http_403"),
    ("http 403", "http_403"),
    ("status code 403", "http_403"),
    ("403 forbidden", "http_403"),
    ("http error 429", "http_429"),
    ("http 429", "http_429"),
    ("status code 429", "http_429"),
    ("429 too many requests", "http_429"),
)


class TikTokRecoveryError(RuntimeError):
    def __init__(self, classification: str, attempts: int) -> None:
        self.classification = classification
        self.attempts = attempts
        super().__init__(TIKTOK_TEMPORARY_MESSAGE)


class TikTokOperationCancelled(RuntimeError):
    pass


def classify_tiktok_error(platform: str, error: object) -> str | None:
    if platform != "TikTok":
        return None
    text = _normalized_error(error)
    if _has_marker(text, _CANCELLATION_MARKERS):
        return None
    if _has_marker(text, _PERMANENT_MARKERS):
        return None
    for marker, classification in _TRANSIENT_MARKERS:
        if marker in text:
            return classification
    if "challenge" in text and ("temporary" in text or "webpage" in text):
        return "challenge_response"
    return None


def is_tiktok_retryable_error(platform: str, error: object) -> bool:
    return classify_tiktok_error(platform, error) is not None


def is_tiktok_permanent_error(platform: str, error: object) -> bool:
    if platform != "TikTok":
        return False
    return _has_marker(_normalized_error(error), _PERMANENT_MARKERS)


def is_tiktok_cancellation_error(platform: str, error: object) -> bool:
    if platform != "TikTok":
        return False
    return isinstance(error, TikTokOperationCancelled) or _has_marker(
        _normalized_error(error),
        _CANCELLATION_MARKERS,
    )


def tiktok_user_message(error: object) -> str:
    if isinstance(error, TikTokRecoveryError) or is_tiktok_retryable_error(
        "TikTok", error
    ):
        return TIKTOK_TEMPORARY_MESSAGE
    return TIKTOK_UNAVAILABLE_MESSAGE


def run_ytdlp_with_tiktok_recovery(
    *,
    platform: str,
    operation: str,
    purpose: str,
    url: str,
    extra_options: dict[str, Any] | None,
    action: Callable[[Any], T],
    cancellation_check: Callable[[], bool] | None = None,
    before_retry: Callable[[], None] | None = None,
    options_callback: Callable[[dict[str, Any]], None] | None = None,
    embed_fallback: Callable[[TikTokRecoveryError], T] | None = None,
    sleep_func: Callable[[float], None] | None = None,
    jitter_func: Callable[[float, float], float] | None = None,
) -> T:
    """Run a yt-dlp action with fresh, bounded TikTok sessions."""
    if platform != "TikTok":
        options = build_ytdlp_options(platform, purpose, extra_options)
        if options_callback is not None:
            options_callback(options)
        return _run_fresh_ytdlp(options, action)

    sleep = sleep_func or time.sleep
    jitter = jitter_func or random.uniform
    video_id = sanitized_tiktok_video_id(url)

    for attempt in range(1, TIKTOK_MAX_ATTEMPTS + 1):
        _raise_if_cancelled(cancellation_check)
        options = build_ytdlp_options(platform, purpose, extra_options)
        target_name = "chrome"
        target = options.get("impersonate")
        if attempt == TIKTOK_MAX_ATTEMPTS:
            safari_target = build_supported_impersonate_target("safari")
            if safari_target is not None:
                target_name = "safari"
                target = safari_target
        if target is not None:
            options["impersonate"] = target
        else:
            options.pop("impersonate", None)
        if options_callback is not None:
            options_callback(options)

        logger.info(
            "TikTok yt-dlp attempt. platform=%s operation=%s attempt=%s "
            "retry_classification=%s video_id=%s outcome=%s impersonate=%s",
            platform,
            operation,
            attempt,
            "initial" if attempt == 1 else "retry",
            video_id,
            "started",
            target_name if target is not None else "unavailable",
        )
        try:
            result = _run_fresh_ytdlp(options, action)
        except Exception as exc:
            if is_tiktok_cancellation_error(platform, exc):
                logger.info(
                    "TikTok yt-dlp attempt. platform=%s operation=%s attempt=%s "
                    "retry_classification=%s video_id=%s outcome=%s",
                    platform,
                    operation,
                    attempt,
                    "cancelled",
                    video_id,
                    "stopped",
                )
                raise

            classification = classify_tiktok_error(platform, exc)
            if classification is None:
                outcome = "permanent_failure" if is_tiktok_permanent_error(
                    platform, exc
                ) else "non_retryable_failure"
                logger.info(
                    "TikTok yt-dlp attempt. platform=%s operation=%s attempt=%s "
                    "retry_classification=%s video_id=%s outcome=%s",
                    platform,
                    operation,
                    attempt,
                    "permanent" if outcome == "permanent_failure" else "unknown",
                    video_id,
                    outcome,
                )
                raise

            logger.info(
                "TikTok yt-dlp attempt. platform=%s operation=%s attempt=%s "
                "retry_classification=%s video_id=%s outcome=%s",
                platform,
                operation,
                attempt,
                classification,
                video_id,
                "retrying" if attempt < TIKTOK_MAX_ATTEMPTS else "failed",
            )
            if attempt >= TIKTOK_MAX_ATTEMPTS:
                recovery_error = TikTokRecoveryError(classification, attempt)
                if embed_fallback is None:
                    raise recovery_error from None
                _raise_if_cancelled(cancellation_check)
                logger.info(
                    "TikTok embed fallback start. platform=%s operation=%s "
                    "attempt=%s retry_classification=%s video_id=%s "
                    "fallback_used=%s result=%s",
                    platform,
                    operation,
                    attempt,
                    classification,
                    video_id,
                    True,
                    "started",
                )
                try:
                    result = embed_fallback(recovery_error)
                except TikTokOperationCancelled:
                    raise
                except Exception:
                    logger.info(
                        "TikTok embed fallback finish. platform=%s operation=%s "
                        "attempt=%s retry_classification=%s video_id=%s "
                        "fallback_used=%s result=%s",
                        platform,
                        operation,
                        attempt,
                        classification,
                        video_id,
                        True,
                        "failed",
                    )
                    raise recovery_error from None
                logger.info(
                    "TikTok embed fallback finish. platform=%s operation=%s "
                    "attempt=%s retry_classification=%s video_id=%s "
                    "fallback_used=%s result=%s",
                    platform,
                    operation,
                    attempt,
                    classification,
                    video_id,
                    True,
                    "succeeded",
                )
                return result

            if before_retry is not None:
                before_retry()
            _raise_if_cancelled(cancellation_check)
            delay = (
                1.0 + jitter(0.0, 0.25)
                if attempt == 1
                else 2.0 + jitter(0.0, 1.0)
            )
            sleep(delay)
            _raise_if_cancelled(cancellation_check)
            continue

        logger.info(
            "TikTok yt-dlp attempt. platform=%s operation=%s attempt=%s "
            "retry_classification=%s video_id=%s outcome=%s",
            platform,
            operation,
            attempt,
            "initial" if attempt == 1 else "retry",
            video_id,
            "succeeded",
        )
        return result

    raise AssertionError("TikTok retry loop exceeded its fixed attempt limit")


def sanitized_tiktok_video_id(url: str) -> str:
    match = re.search(r"(?:/video/|[?&](?:item_id|video_id)=)(\d{5,})", url)
    return match.group(1) if match else "unknown"


def _run_fresh_ytdlp(options: dict[str, Any], action: Callable[[Any], T]) -> T:
    import yt_dlp

    with yt_dlp.YoutubeDL(options) as ydl:
        return action(ydl)


def _raise_if_cancelled(cancellation_check: Callable[[], bool] | None) -> None:
    if cancellation_check is not None and cancellation_check():
        raise TikTokOperationCancelled("TikTok operation cancelled.")


def _normalized_error(error: object) -> str:
    return " ".join(str(error).lower().split())


def _has_marker(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)
