from fastapi import APIRouter, BackgroundTasks, Depends

from app.core.security import get_optional_api_key
from app.models.download_models import (
    DownloadRequest,
    DownloadStartResponse,
    DownloadStatusResponse,
)
from app.services.download_service import download_service

router = APIRouter(tags=["download"])


@router.post("/download", response_model=DownloadStartResponse)
async def start_download(
    payload: DownloadRequest,
    background_tasks: BackgroundTasks,
    _api_key: str | None = Depends(get_optional_api_key),
) -> DownloadStartResponse:
    response = download_service.create_job(payload)
    background_tasks.add_task(download_service.process_job, response.jobId)
    return response


@router.get("/download/status/{job_id}", response_model=DownloadStatusResponse)
async def download_status(
    job_id: str,
    _api_key: str | None = Depends(get_optional_api_key),
) -> DownloadStatusResponse:
    return download_service.get_status(job_id)
