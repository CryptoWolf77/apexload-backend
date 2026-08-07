from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from app.api.routes import (
    admin_instagram,
    admin_youtube,
    analyze,
    debug,
    download,
    editor,
    files,
    health,
    reviewer_access,
    takedown,
)
from app.core.config import get_settings
from app.services.instagram_cookie_health import (
    initialize_instagram_cookie_storage,
    start_instagram_cookie_health_scheduler,
    stop_instagram_cookie_health_scheduler,
)
from app.services.youtube_proxy_manager import youtube_proxy_manager

settings = get_settings()
cors_origins = list(
    dict.fromkeys(
        origin
        for origin in (
            "https://apexload.org",
            "https://www.apexload.org",
            "https://api.apexload.org",
            "http://localhost",
            "http://127.0.0.1",
            *settings.cors_origins,
        )
        if origin != "*"
    )
)

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="ApexLoad Version 1.3.1 backend with real yt-dlp analyze and download fixes.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix=settings.api_prefix)
app.include_router(reviewer_access.router, prefix=settings.api_prefix)
app.include_router(analyze.router, prefix=settings.api_prefix)
app.include_router(download.router, prefix=settings.api_prefix)
app.include_router(editor.router, prefix=settings.api_prefix)
app.include_router(files.router, prefix=settings.api_prefix)
app.include_router(debug.router, prefix=f"{settings.api_prefix}/debug", tags=["debug"])
app.include_router(admin_instagram.router)
app.include_router(admin_youtube.router)
app.include_router(takedown.router)


@app.on_event("startup")
async def startup_background_services() -> None:
    initialize_instagram_cookie_storage()
    start_instagram_cookie_health_scheduler()
    youtube_proxy_manager.start_background()
    if settings.youtube_proxy_enabled and settings.youtube_proxy_prewarm_enabled:
        await run_in_threadpool(
            youtube_proxy_manager.wait_until_ready,
            settings.youtube_proxy_startup_wait_seconds,
        )


@app.on_event("shutdown")
async def shutdown_background_services() -> None:
    await run_in_threadpool(youtube_proxy_manager.stop_background)
    await stop_instagram_cookie_health_scheduler()


@app.get("/")
async def root() -> dict[str, bool | str]:
    return {
        "success": True,
        "message": "ApexLoad backend skeleton. Visit /docs or /api/health.",
    }
