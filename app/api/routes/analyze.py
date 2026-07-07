import logging

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.core.security import get_optional_api_key
from app.models.analyze_models import AnalyzeRequest, AnalyzeResponse
from app.services.mock_analyze_service import MockAnalyzeService
from app.services.instagram_error_classifier import classify_instagram_error
from app.services.instagram_safety_service import instagram_safety_service
from app.services.ytdlp_analyze_service import (
    AnalyzeServiceError,
    InstagramAuthRequiredError,
    UnsupportedUrlError,
    YouTubeAuthRequiredError,
    YtDlpAnalyzeService,
)
from app.utils.platform_detector import detect_platform

router = APIRouter(tags=["analyze"])
logger = logging.getLogger("apexload.analyze")
mock_service = MockAnalyzeService()
ytdlp_service = YtDlpAnalyzeService()


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze_link(
    payload: AnalyzeRequest,
    _api_key: str | None = Depends(get_optional_api_key),
) -> AnalyzeResponse:
    settings = get_settings()
    is_instagram = detect_platform(payload.url) == "Instagram"
    decision = None
    if is_instagram:
        decision = instagram_safety_service.begin_request()
        if not decision.allowed:
            return AnalyzeResponse(
                success=False,
                source="yt_dlp",
                platform="Instagram",
                error=decision.code,
                code=decision.code,
                message=decision.message,
            )
    try:
        response = await run_in_threadpool(ytdlp_service.analyze, payload.url)
        if is_instagram:
            if response.success:
                instagram_safety_service.finish_success(decision)
            else:
                raw_error = response.message or response.error or "Instagram analyze failed"
                preview = classify_instagram_error(raw_error)
                if preview.category == "media_unavailable":
                    instagram_safety_service.finish_neutral(decision)
                    return response
                classification = instagram_safety_service.finish_failure(
                    raw_error,
                    decision,
                )
                return AnalyzeResponse(
                    success=False,
                    source=response.source,
                    platform="Instagram",
                    error="INSTAGRAM_TEMPORARILY_UNAVAILABLE",
                    code="INSTAGRAM_TEMPORARILY_UNAVAILABLE",
                    message=classification.safe_user_message,
                )
        return response
    except YouTubeAuthRequiredError as exc:
        return AnalyzeResponse(
            success=False,
            source="yt_dlp",
            error=exc.error,
            message=exc.message,
        )
    except InstagramAuthRequiredError as exc:
        if is_instagram:
            classification = instagram_safety_service.finish_failure(exc.message, decision)
            return AnalyzeResponse(
                success=False,
                source="yt_dlp",
                platform="Instagram",
                error="INSTAGRAM_TEMPORARILY_UNAVAILABLE",
                code="INSTAGRAM_TEMPORARILY_UNAVAILABLE",
                message=classification.safe_user_message,
            )
        return AnalyzeResponse(
            success=False,
            source="yt_dlp",
            error=exc.error,
            message=exc.message,
        )
    except UnsupportedUrlError as exc:
        if is_instagram and decision:
            instagram_safety_service.finish_neutral(decision)
        return AnalyzeResponse(
            success=False,
            source="yt_dlp",
            error=exc.error,
            message=exc.message,
        )
    except AnalyzeServiceError as exc:
        if is_instagram:
            classification = instagram_safety_service.finish_failure(
                exc.raw_message or exc.message,
                decision,
            )
            return AnalyzeResponse(
                success=False,
                source="yt_dlp",
                platform="Instagram",
                error="INSTAGRAM_TEMPORARILY_UNAVAILABLE",
                code="INSTAGRAM_TEMPORARILY_UNAVAILABLE",
                message=classification.safe_user_message,
            )
        if settings.use_mock_analyze_fallback:
            logger.info("Using mock analyze fallback after yt-dlp analyze failure")
            return mock_service.analyze(payload.url)
        return AnalyzeResponse(
            success=False,
            source="yt_dlp",
            error=exc.error,
            message=exc.message,
        )
    except Exception as exc:
        if is_instagram:
            classification = instagram_safety_service.finish_failure(exc, decision)
            return AnalyzeResponse(
                success=False,
                source="yt_dlp",
                platform="Instagram",
                error="INSTAGRAM_TEMPORARILY_UNAVAILABLE",
                code="INSTAGRAM_TEMPORARILY_UNAVAILABLE",
                message=classification.safe_user_message,
            )
        raise
