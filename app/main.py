from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import analyze, download, files, health
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="ApexLoad Version 1.2A backend skeleton with demo API responses.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix=settings.api_prefix)
app.include_router(analyze.router, prefix=settings.api_prefix)
app.include_router(download.router, prefix=settings.api_prefix)
app.include_router(files.router, prefix=settings.api_prefix)


@app.get("/")
async def root() -> dict[str, bool | str]:
    return {
        "success": True,
        "message": "ApexLoad backend skeleton. Visit /docs or /api/health.",
    }

