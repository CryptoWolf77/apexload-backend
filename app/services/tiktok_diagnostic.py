"""Safe internal TikTok diagnostic.

Usage: python -m app.services.tiktok_diagnostic <public-tiktok-url>
"""

from __future__ import annotations

import argparse
import json
from typing import Any
from urllib.parse import urlparse

from app.services.tiktok_embed_service import (
    parse_tiktok_video_id,
    tiktok_embed_service,
)
from app.services.tiktok_recovery import (
    run_ytdlp_with_tiktok_recovery,
    tiktok_user_message,
)


def diagnose_tiktok_url(url: str) -> dict[str, Any]:
    video_id = parse_tiktok_video_id(url)
    attempts = 0
    fallback_used = False
    media_host: str | None = None

    def extract(ydl):
        nonlocal attempts
        attempts += 1
        return ydl.extract_info(url, download=False)

    def fallback(_error):
        nonlocal fallback_used, media_host
        fallback_used = True
        media = tiktok_embed_service.resolve(url)
        media_host = media.media_host
        return media.as_ytdlp_info()

    try:
        info = run_ytdlp_with_tiktok_recovery(
            platform="TikTok",
            operation="diagnostic",
            purpose="analyze",
            url=url,
            extra_options=None,
            action=extract,
            embed_fallback=fallback,
        )
    except Exception as exc:
        return {
            "videoId": video_id,
            "normalExtraction": "failed",
            "retryResult": "failed",
            "attempts": attempts,
            "embedFallback": "failed" if fallback_used else "not_used",
            "resolvedMediaHost": media_host,
            "result": "failed",
            "message": tiktok_user_message(exc),
        }

    if not media_host and isinstance(info, dict):
        media_host = _media_host_from_info(info)
    return {
        "videoId": video_id,
        "normalExtraction": "succeeded" if attempts == 1 else "failed",
        "retryResult": (
            "succeeded" if 1 < attempts <= 3 and not fallback_used else "not_needed"
            if attempts == 1
            else "failed"
        ),
        "attempts": attempts,
        "embedFallback": "succeeded" if fallback_used else "not_used",
        "resolvedMediaHost": media_host,
        "result": "succeeded",
    }


def _media_host_from_info(info: dict[str, Any]) -> str | None:
    candidates = [info.get("url")]
    formats = info.get("formats")
    if isinstance(formats, list):
        candidates.extend(
            item.get("url") for item in formats if isinstance(item, dict)
        )
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        host = urlparse(candidate).hostname
        if host:
            return host
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a sanitized ApexLoad TikTok extraction diagnostic."
    )
    parser.add_argument("url", help="Public TikTok video URL")
    args = parser.parse_args()
    print(json.dumps(diagnose_tiktok_url(args.url), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
