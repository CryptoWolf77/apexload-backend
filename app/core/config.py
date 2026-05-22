import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


class Settings:
    app_name: str = "ApexLoad Backend"
    app_version: str = "1.3.2"
    api_prefix: str = "/api"
    api_key: str | None = os.getenv("API_KEY")
    environment: str = os.getenv("ENVIRONMENT", "development")
    use_mock_analyze_fallback: bool = (
        os.getenv("USE_MOCK_ANALYZE_FALLBACK", "true").lower()
        in {"1", "true", "yes", "on"}
    )
    instagram_auth_mode: str = os.getenv(
        "INSTAGRAM_AUTH_MODE",
        "cookiefile",
    ).lower()
    instagram_cookie_file: str = os.getenv(
        "INSTAGRAM_COOKIE_FILE",
        "/app/secrets/instagram_cookies.txt",
    )
    # Backward-compatible aliases for existing deployments.
    enable_instagram_cookies: bool = (
        os.getenv("ENABLE_INSTAGRAM_COOKIES", "").lower()
        in {"1", "true", "yes", "on"}
    )
    instagram_cookies_file: str = os.getenv(
        "INSTAGRAM_COOKIES_FILE",
        instagram_cookie_file,
    )
    ytdlp_cookies_from_browser_enable: bool = (
        os.getenv("YTDLP_COOKIES_FROM_BROWSER_ENABLE", "false").lower()
        in {"1", "true", "yes", "on"}
    )
    ytdlp_cookies_browser: str = os.getenv("YTDLP_COOKIES_BROWSER", "chrome")
    ytdlp_cookies_browser_profile: str = os.getenv(
        "YTDLP_COOKIES_BROWSER_PROFILE",
        "",
    )
    ytdlp_cookies_browser_keyring: str = os.getenv(
        "YTDLP_COOKIES_BROWSER_KEYRING",
        "",
    )
    admin_api_key: str = os.getenv("ADMIN_API_KEY", "")
    ytdlp_update_policy: str = os.getenv("YTDLP_UPDATE_POLICY", "manual")
    ffmpeg_location: str = os.getenv("FFMPEG_LOCATION", "")
    enable_youtube_cookies: bool = (
        os.getenv("ENABLE_YOUTUBE_COOKIES", "false").lower()
        in {"1", "true", "yes", "on"}
    )
    youtube_cookies_file: str = os.getenv("YOUTUBE_COOKIES_FILE", "")
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
