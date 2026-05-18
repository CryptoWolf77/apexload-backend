import logging

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.core.security import get_optional_api_key
from app.models.analyze_models import AnalyzeRequest, AnalyzeResponse
from app.services.mock_analyze_service import MockAnalyzeService
from app.services.ytdlp_analyze_service import (
    AnalyzeServiceError,
    InstagramAuthRequiredError,
    UnsupportedUrlError,
    YtDlpAnalyzeService,
)

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
    try:
        return await run_in_threadpool(ytdlp_service.analyze, payload.url)
    except InstagramAuthRequiredError as exc:
        if settings.use_mock_analyze_fallback:
            logger.info("Using mock analyze fallback after Instagram auth/block response")
            response = mock_service.analyze(payload.url)
            response.error = exc.error
            response.message = exc.message
            return response
        return AnalyzeResponse(
            success=False,
            source="yt_dlp",
            error=exc.error,
            message=exc.message,
        )
    except UnsupportedUrlError as exc:
        return AnalyzeResponse(
            success=False,
            source="yt_dlp",
            error=exc.error,
            message=exc.message,
        )
    except AnalyzeServiceError as exc:
        if settings.use_mock_analyze_fallback:
            logger.info("Using mock analyze fallback after yt-dlp analyze failure")
            return mock_service.analyze(payload.url)
        return AnalyzeResponse(
            success=False,
            source="yt_dlp",
            error=exc.error,
            message=exc.message,
        )
