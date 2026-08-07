import socket
from urllib.parse import urlparse

from fastapi import APIRouter

from app.core.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check() -> dict[str, bool | str]:
    settings = get_settings()
    provider = urlparse(settings.bgutil_base_url)
    provider_ready = False
    try:
        with socket.create_connection((provider.hostname or "127.0.0.1", provider.port or 4416), timeout=0.25):
            provider_ready = True
    except OSError:
        pass
    return {
        "success": True,
        "status": "ok",
        "message": "ApexLoad backend is running",
        "potProviderReady": provider_ready,
    }
