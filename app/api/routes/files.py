from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.core.security import get_optional_api_key
from app.services.download_service import download_service

router = APIRouter(tags=["files"])


@router.get("/file/{file_id}")
async def get_file(
    file_id: str,
    _api_key: str | None = Depends(get_optional_api_key),
) -> FileResponse:
    file_path = download_service.get_file_path(file_id)
    if file_path is None:
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path=file_path, filename=file_path.name)
