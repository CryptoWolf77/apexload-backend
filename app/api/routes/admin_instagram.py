from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.core.admin_rate_limiter import enforce_admin_rate_limit
from app.core.config import get_settings
from app.services.instagram_auth_service import (
    delete_instagram_cookie_file,
    get_instagram_auth_status,
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
    function headers(){ return {'Content-Type':'application/json','X-Admin-Key': keyInput.value}; }
    async function api(path, options={}){
      const res = await fetch(path, {...options, headers: {...headers(), ...(options.headers||{})}});
      const data = await res.json().catch(() => ({status: res.status}));
      statusBox.textContent = JSON.stringify(data, null, 2);
      return data;
    }
    function loadStatus(){ api('/api/admin/instagram/auth-status'); }
    function uploadCookies(){
      api('/api/admin/instagram/upload-cookies', {method:'POST', body: JSON.stringify({cookiesText: document.getElementById('cookies').value})});
      document.getElementById('cookies').value = '';
    }
    function validateCookies(){ api('/api/admin/instagram/validate-cookies', {method:'POST', body: JSON.stringify({})}); }
    function deleteCookies(){ api('/api/admin/instagram/cookies', {method:'DELETE'}); }
  </script>
</body>
</html>
"""
