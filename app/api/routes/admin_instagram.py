from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.core.admin_rate_limiter import enforce_admin_rate_limit
from app.core.config import get_settings
from app.services.instagram_cookie_health import (
    MAX_UPLOAD_BYTES,
    check_instagram_cookies,
    latest_health_result,
    safe_config,
    validate_uploaded_cookie_file,
)
from app.services.instagram_auth_service import (
    delete_instagram_cookie_file,
    get_instagram_auth_status,
    instagram_cookie_path,
    save_instagram_cookie_file_securely,
    test_instagram_cookie_with_ytdlp,
)

router = APIRouter(tags=["admin-instagram"])


class UploadCookiesJson(BaseModel):
    cookiesText: str


class ValidateCookiesRequest(BaseModel):
    testUrl: str | None = None


def require_admin_key(x_admin_key: str | None) -> None:
    settings = get_settings()
    if not settings.admin_api_key:
        raise HTTPException(status_code=503, detail="Instagram admin API is disabled.")
    if not x_admin_key or x_admin_key != settings.admin_api_key:
        raise HTTPException(status_code=401, detail="Invalid admin key.")


def require_admin_bearer(authorization: str | None) -> None:
    settings = get_settings()
    token = settings.admin_api_token
    if not token:
        raise HTTPException(status_code=503, detail="Admin cookie API is disabled.")
    prefix = "Bearer "
    if not authorization or not authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail="Missing admin bearer token.")
    if authorization.removeprefix(prefix).strip() != token:
        raise HTTPException(status_code=401, detail="Invalid admin bearer token.")


@router.get("/admin/instagram/cookies/status")
async def cookie_health_status(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    require_admin_bearer(authorization)
    enforce_admin_rate_limit(request, "instagram_cookies_status", 30, 60)
    return {"success": True, **latest_health_result()}


@router.post("/admin/instagram/cookies/check")
async def cookie_health_check(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    require_admin_bearer(authorization)
    enforce_admin_rate_limit(request, "instagram_cookies_check", 10, 60)
    return {"success": True, **check_instagram_cookies()}


@router.post("/admin/instagram/cookies/upload")
async def cookie_health_upload(
    request: Request,
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    require_admin_bearer(authorization)
    enforce_admin_rate_limit(request, "instagram_cookies_upload", 5, 600)
    if not (file.filename or "").lower().endswith(".txt"):
        raise HTTPException(status_code=400, detail="Only .txt cookie files are accepted.")
    if file.content_type and file.content_type not in {
        "text/plain",
        "application/octet-stream",
    }:
        raise HTTPException(status_code=400, detail="Only text cookie files are accepted.")

    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="Cookie file is too large.")
    content = raw.decode("utf-8", errors="ignore")
    active_path = instagram_cookie_path()
    active_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = active_path.with_name(f".{active_path.name}.admin_upload.tmp")
    temp_path.write_text(content, encoding="utf-8")
    try:
        validation = validate_uploaded_cookie_file(temp_path)
        if not validation.get("is_valid"):
            raise HTTPException(
                status_code=400,
                detail=validation.get("message") or "Cookie file is invalid.",
            )
        status = save_instagram_cookie_file_securely(content)
        fresh_health = check_instagram_cookies()
        return {
            "success": bool(status["cookieFileLooksValid"]),
            "message": "Instagram cookies uploaded and validated.",
            "authStatus": status,
            "health": fresh_health,
        }
    finally:
        _safe_unlink(temp_path)


@router.get("/admin/instagram/cookies/config")
async def cookie_health_config(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    require_admin_bearer(authorization)
    enforce_admin_rate_limit(request, "instagram_cookies_config", 30, 60)
    return {"success": True, **safe_config()}


@router.get("/api/admin/instagram/auth-status")
async def auth_status(
    request: Request,
    x_admin_key: str | None = Header(default=None),
) -> dict[str, Any]:
    require_admin_key(x_admin_key)
    enforce_admin_rate_limit(request, "instagram_auth_status", 30, 60)
    return {"success": True, **get_instagram_auth_status()}


@router.post("/api/admin/instagram/upload-cookies")
async def upload_cookies(
    request: Request,
    x_admin_key: str | None = Header(default=None),
) -> dict[str, Any]:
    require_admin_key(x_admin_key)
    enforce_admin_rate_limit(request, "instagram_upload_cookies", 5, 600)
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

    status = save_instagram_cookie_file_securely(cookies_text)
    return {"success": bool(status["cookieFileLooksValid"]), **status}


@router.post("/api/admin/instagram/validate-cookies")
async def validate_cookies(
    request: Request,
    payload: ValidateCookiesRequest,
    x_admin_key: str | None = Header(default=None),
) -> dict[str, Any]:
    require_admin_key(x_admin_key)
    enforce_admin_rate_limit(request, "instagram_validate_cookies", 10, 60)
    status = test_instagram_cookie_with_ytdlp(payload.testUrl)
    return {
        "success": status["lastValidationStatus"] == "valid",
        "ytDlpVersion": status["ytDlpVersion"],
        "authMode": status["authMode"],
        "message": status["reason"],
        **status,
    }


@router.delete("/api/admin/instagram/cookies")
async def delete_cookies(
    x_admin_key: str | None = Header(default=None),
) -> dict[str, Any]:
    require_admin_key(x_admin_key)
    status = delete_instagram_cookie_file()
    return {"success": True, **status}


@router.get("/admin/instagram", response_class=HTMLResponse)
async def instagram_admin_page() -> str:
    if not get_settings().admin_api_key:
        return "<h1>ApexLoad Instagram Admin</h1><p>Admin API is disabled.</p>"
    return """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>ApexLoad Instagram Admin</title>
  <style>
    body { margin: 0; font-family: Inter, system-ui, sans-serif; background: #0b1020; color: #fff; }
    main { max-width: 860px; margin: 0 auto; padding: 32px 18px; }
    section { background: #151b2e; border: 1px solid #2d3655; border-radius: 18px; padding: 18px; margin: 14px 0; }
    input, textarea { width: 100%; box-sizing: border-box; border-radius: 12px; border: 1px solid #3b4567; background: #0f1629; color: #fff; padding: 12px; }
    textarea { min-height: 180px; font-family: ui-monospace, monospace; }
    button { border: 0; border-radius: 999px; padding: 11px 16px; color: #fff; background: linear-gradient(90deg,#6c63ff,#00d4ff); font-weight: 800; cursor: pointer; margin: 6px 6px 6px 0; }
    button.danger { background: #ef4444; }
    pre { white-space: pre-wrap; background: #0f1629; border-radius: 12px; padding: 12px; overflow: auto; }
    small { color: #aab3c5; }
  </style>
</head>
<body>
  <main>
    <h1>ApexLoad Instagram Admin</h1>
    <small>Admin key is stored in sessionStorage only. Cookie contents are never displayed after upload.</small>
    <section>
      <label>Admin key</label>
      <input id="key" type="password" placeholder="X-Admin-Key" />
      <button onclick="saveKey()">Use key</button>
      <button onclick="loadStatus()">Refresh status</button>
    </section>
    <section>
      <h2>Status</h2>
      <pre id="status">Not loaded</pre>
    </section>
    <section>
      <h2>Upload Netscape cookies</h2>
      <textarea id="cookies" placeholder="# Netscape HTTP Cookie File..."></textarea>
      <button onclick="uploadCookies()">Upload cookies</button>
      <button onclick="validateCookies()">Validate cookies</button>
      <button class="danger" onclick="deleteCookies()">Delete cookies</button>
    </section>
  </main>
  <script>
    const keyInput = document.getElementById('key');
    const statusBox = document.getElementById('status');
    keyInput.value = sessionStorage.getItem('apexloadAdminKey') || '';
    function saveKey(){ sessionStorage.setItem('apexloadAdminKey', keyInput.value); loadStatus(); }
    function authHeaders(){ return {'X-Admin-Key': keyInput.value, 'Authorization': 'Bearer ' + keyInput.value}; }
    function headers(){ return {'Content-Type':'application/json', ...authHeaders()}; }
    async function api(path, options={}){
      const res = await fetch(path, {...options, headers: {...headers(), ...(options.headers||{})}});
      const data = await res.json().catch(() => ({status: res.status}));
      statusBox.textContent = JSON.stringify(data, null, 2);
      return data;
    }
    function loadStatus(){ api('/admin/instagram/cookies/status'); }
    async function uploadCookies(){
      const form = new FormData();
      const blob = new Blob([document.getElementById('cookies').value], {type: 'text/plain'});
      form.append('file', blob, 'instagram_cookies.txt');
      const res = await fetch('/admin/instagram/cookies/upload', {method:'POST', headers: authHeaders(), body: form});
      const data = await res.json().catch(() => ({status: res.status}));
      statusBox.textContent = JSON.stringify(data, null, 2);
      document.getElementById('cookies').value = '';
    }
    function validateCookies(){ api('/admin/instagram/cookies/check', {method:'POST', body: JSON.stringify({})}); }
    function deleteCookies(){ api('/api/admin/instagram/cookies', {method:'DELETE'}); }
  </script>
</body>
</html>
"""


def _safe_unlink(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass
