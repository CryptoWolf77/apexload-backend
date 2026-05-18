import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


class Settings:
    app_name: str = "ApexLoad Backend"
    app_version: str = "1.2B"
    api_prefix: str = "/api"
    api_key: str | None = os.getenv("API_KEY")
    environment: str = os.getenv("ENVIRONMENT", "development")
    use_mock_analyze_fallback: bool = (
        os.getenv("USE_MOCK_ANALYZE_FALLBACK", "true").lower()
        in {"1", "true", "yes", "on"}
    )
    enable_instagram_cookies: bool = (
        os.getenv("ENABLE_INSTAGRAM_COOKIES", "false").lower()
        in {"1", "true", "yes", "on"}
    )
    instagram_cookies_file: str = os.getenv("INSTAGRAM_COOKIES_FILE", "")
    cors_origins: list[str] = [
        origin.strip()
        for origin in os.getenv(
            "CORS_ORIGINS",
            "http://localhost:3000,http://localhost:5173,http://localhost:8080,http://127.0.0.1:8080",
        ).split(",")
        if origin.strip()
    ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
