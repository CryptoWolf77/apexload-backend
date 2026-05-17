from fastapi import APIRouter, Depends

from app.core.security import get_optional_api_key
from app.models.download_models import FileEndpointResponse

router = APIRouter(tags=["files"])


@router.get("/file/{file_id}", response_model=FileEndpointResponse)
async def get_file(
    file_id: str,
    _api_key: str | None = Depends(get_optional_api_key),
) -> FileEndpointResponse:
    return FileEndpointResponse(
        success=True,
        fileId=file_id,
        message="Demo file endpoint. Real file serving will be added in Version 1.2C.",
    )

