import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from app.core.security import get_optional_api_key
from app.models.editor_models import (
    EditorRequest,
    EditorStartResponse,
    EditorStatusResponse,
)
from app.services.editor_service import EDITOR_ERROR_MESSAGE, editor_service

router = APIRouter(tags=["editor"])
logger = logging.getLogger("apexload.editor.routes")


def _start_editor_job(
    operation: str,
    payload: EditorRequest,
    background_tasks: BackgroundTasks,
) -> EditorStartResponse:
    logger.info(
        "Editor route hit: %s operation=%s fileId=%s downloadUrl=%s",
        operation,
        operation,
        (payload.fileId or "").strip() or None,
        (payload.downloadUrl or "").strip() or None,
    )
    try:
        response = editor_service.create_job(operation, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    background_tasks.add_task(editor_service.process_job, response.jobId)
    return response


@router.post("/editor/trim", response_model=EditorStartResponse)
async def trim_video(
    payload: EditorRequest,
    background_tasks: BackgroundTasks,
    _api_key: str | None = Depends(get_optional_api_key),
) -> EditorStartResponse:
    return _start_editor_job("trim", payload, background_tasks)


@router.post("/editor/extract-audio", response_model=EditorStartResponse)
async def extract_audio(
    payload: EditorRequest,
    background_tasks: BackgroundTasks,
    _api_key: str | None = Depends(get_optional_api_key),
) -> EditorStartResponse:
    return _start_editor_job("extract-audio", payload, background_tasks)


@router.post("/editor/mute", response_model=EditorStartResponse)
async def mute_video(
    payload: EditorRequest,
    background_tasks: BackgroundTasks,
    _api_key: str | None = Depends(get_optional_api_key),
) -> EditorStartResponse:
    return _start_editor_job("mute", payload, background_tasks)


@router.post("/editor/compress", response_model=EditorStartResponse)
async def compress_video(
    payload: EditorRequest,
    background_tasks: BackgroundTasks,
    _api_key: str | None = Depends(get_optional_api_key),
) -> EditorStartResponse:
    return _start_editor_job("compress", payload, background_tasks)


@router.post("/editor/convert", response_model=EditorStartResponse)
async def convert_video(
    payload: EditorRequest,
    background_tasks: BackgroundTasks,
    _api_key: str | None = Depends(get_optional_api_key),
) -> EditorStartResponse:
    return _start_editor_job("convert", payload, background_tasks)


@router.get("/editor/status/{job_id}", response_model=EditorStatusResponse)
async def editor_status(
    job_id: str,
    _api_key: str | None = Depends(get_optional_api_key),
) -> EditorStatusResponse:
    status = editor_service.get_status(job_id)
    if status.status == "failed" and not status.error:
        status.error = EDITOR_ERROR_MESSAGE
    return status
