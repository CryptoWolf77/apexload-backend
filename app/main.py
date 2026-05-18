from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import analyze, debug, download, files, health
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="ApexLoad Version 1.2C backend with real yt-dlp analyze and download jobs.",
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
app.include_router(files.router, prefix=settings.api_prefix)
app.include_router(debug.router, prefix=f"{settings.api_prefix}/debug", tags=["debug"])


@app.get("/")
async def root() -> dict[str, bool | str]:
    return {
        "success": True,
        "message": "ApexLoad backend skeleton. Visit /docs or /api/health.",
    }
