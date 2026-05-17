from fastapi import APIRouter, Depends

from app.core.security import get_optional_api_key
from app.models.analyze_models import AnalyzeRequest, AnalyzeResponse
from app.services.mock_analyze_service import MockAnalyzeService

router = APIRouter(tags=["analyze"])
service = MockAnalyzeService()


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze_link(
    payload: AnalyzeRequest,
    _api_key: str | None = Depends(get_optional_api_key),
) -> AnalyzeResponse:
    return service.analyze(payload.url)

