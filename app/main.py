from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    admin_instagram,
    admin_youtube,
    analyze,
    debug,
    download,
    editor,
    files,
    health,
)
from app.core.config import get_settings
from app.services.instagram_cookie_health import (
    initialize_instagram_cookie_storage,
    start_instagram_cookie_health_scheduler,
    stop_instagram_cookie_health_scheduler,
)

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="ApexLoad Version 1.3.1 backend with real yt-dlp analyze and download fixes.",
)

app.add_middleware(
    CORSMiddleware,
    # TODO: Before production release, restrict CORS origins to official
    # app/website domains only.
    allow_origins=[
        "https://apexload.org",
        "https://www.apexload.org",
        "https://api.apexload.org",
        "http://localhost",
        "http://127.0.0.1",
        *settings.cors_origins,
    ],
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix=settings.api_prefix)
app.include_router(analyze.router, prefix=settings.api_prefix)
app.include_router(download.router, prefix=settings.api_prefix)
app.include_router(editor.router, prefix=settings.api_prefix)
app.include_router(files.router, prefix=settings.api_prefix)
app.include_router(debug.router, prefix=f"{settings.api_prefix}/debug", tags=["debug"])
app.include_router(admin_instagram.router)
app.include_router(admin_youtube.router)


@app.on_event("startup")
async def startup_cookie_health() -> None:
    initialize_instagram_cookie_storage()
    start_instagram_cookie_health_scheduler()


@app.on_event("shutdown")
async def shutdown_cookie_health() -> None:
    await stop_instagram_cookie_health_scheduler()


@app.get("/")
async def root() -> dict[str, bool | str]:
    return {
        "success": True,
        "message": "ApexLoad backend skeleton. Visit /docs or /api/health.",
    }
