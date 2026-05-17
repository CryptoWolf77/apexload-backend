from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check() -> dict[str, bool | str]:
    return {
        "success": True,
        "status": "ok",
        "message": "ApexLoad backend is running",
    }

