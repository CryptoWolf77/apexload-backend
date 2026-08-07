import secrets

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import get_settings

router = APIRouter(tags=["reviewer-access"])

_NO_STORE_HEADERS = {"Cache-Control": "no-store"}
_ACCESS_DENIED_MESSAGE = "Reviewer access denied."


class ReviewerAccessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(max_length=256)


@router.post("/reviewer-access/verify")
async def verify_reviewer_access(
    payload: ReviewerAccessRequest,
    response: Response,
) -> dict[str, bool]:
    configured_code = get_settings().play_reviewer_access_code.strip()
    submitted_code = payload.code.strip()

    if not configured_code or not secrets.compare_digest(
        submitted_code.encode("utf-8"),
        configured_code.encode("utf-8"),
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_ACCESS_DENIED_MESSAGE,
            headers=_NO_STORE_HEADERS,
        )

    response.headers.update(_NO_STORE_HEADERS)
    return {"success": True}
