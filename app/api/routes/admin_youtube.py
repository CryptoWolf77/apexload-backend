from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from app.core.admin_rate_limiter import enforce_admin_rate_limit
from app.core.config import get_settings
from app.services.youtube_auth_service import (
    get_youtube_auth_status,
    save_youtube_cookie_file_securely,
    test_youtube_cookie_with_ytdlp,
)

router = APIRouter(tags=["admin-youtube"])


class UploadCookiesJson(BaseModel):
    cookiesText: str


class ValidateCookiesRequest(BaseModel):
    testUrl: str | None = None


def require_admin_key(x_admin_key: str | None) -> None:
    settings = get_settings()
    if not settings.admin_api_key:
        raise HTTPException(status_code=503, detail="Admin API is disabled.")
    if not x_admin_key or x_admin_key != settings.admin_api_key:
        raise HTTPException(status_code=401, detail="Invalid admin key.")


@router.get("/api/admin/youtube/auth-status")
async def auth_status(
    request: Request,
    x_admin_key: str | None = Header(default=None),
) -> dict[str, Any]:
    require_admin_key(x_admin_key)
    enforce_admin_rate_limit(request, "youtube_auth_status", 30, 60)
    return {"success": True, **get_youtube_auth_status()}


@router.post("/api/admin/youtube/upload-cookies")
async def upload_cookies(
    request: Request,
    x_admin_key: str | None = Header(default=None),
) -> dict[str, Any]:
    require_admin_key(x_admin_key)
    enforce_admin_rate_limit(request, "youtube_upload_cookies", 5, 600)
    content_type = request.headers.get("content-type", "")
    cookies_text = ""
    if "multipart/form-data" in content_type:
        form = await request.form()
        file = form.get("file")
        if file is not None and hasattr(file, "read"):
            raw = await file.read()
            cookies_text = raw.decode("utf-8", errors="ignore")
        else:
            cookies_text = str(form.get("cookiesText") or "")
    else:
        data = await request.json()
        payload = UploadCookiesJson.model_validate(data)
        cookies_text = payload.cookiesText

    status = save_youtube_cookie_file_securely(cookies_text)
    return {"success": bool(status["cookieFileLooksValid"]), **status}


@router.post("/api/admin/youtube/validate-cookies")
async def validate_cookies(
    request: Request,
    payload: ValidateCookiesRequest,
    x_admin_key: str | None = Header(default=None),
) -> dict[str, Any]:
    require_admin_key(x_admin_key)
    enforce_admin_rate_limit(request, "youtube_validate_cookies", 10, 60)
    status = test_youtube_cookie_with_ytdlp(payload.testUrl)
    return {
        "success": status["lastValidationStatus"] == "valid",
        "ytDlpVersion": status["ytDlpVersion"],
        "authMode": status["authMode"],
        "message": status["reason"],
        **status,
    }
