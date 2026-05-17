from fastapi import APIRouter, Depends

from app.core.security import get_optional_api_key
from app.models.download_models import (
    DownloadRequest,
    DownloadStartResponse,
    DownloadStatusResponse,
)
from app.services.mock_download_service import MockDownloadService

router = APIRouter(tags=["download"])
service = MockDownloadService()


@router.post("/download", response_model=DownloadStartResponse)
async def start_download(
    payload: DownloadRequest,
    _api_key: str | None = Depends(get_optional_api_key),
) -> DownloadStartResponse:
    return service.create_job(payload)


@router.get("/download/status/{job_id}", response_model=DownloadStatusResponse)
async def download_status(
    job_id: str,
    _api_key: str | None = Depends(get_optional_api_key),
) -> DownloadStatusResponse:
    return service.get_status(job_id)

