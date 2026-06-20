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
    instagram_cookies_path: str = os.getenv(
        "INSTAGRAM_COOKIES_PATH",
        os.getenv(
            "INSTAGRAM_COOKIE_FILE",
            os.getenv("INSTAGRAM_COOKIES_FILE", "data/cookies/instagram_cookies.txt"),
        ),
    )
    instagram_cookie_file: str = os.getenv(
        "INSTAGRAM_COOKIE_FILE",
        instagram_cookies_path,
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
    instagram_healthcheck_url: str = os.getenv("INSTAGRAM_HEALTHCHECK_URL", "")
    instagram_cookie_health_enabled: bool = (
        os.getenv("INSTAGRAM_COOKIE_HEALTH_ENABLED", "true").lower()
        in {"1", "true", "yes", "on"}
    )
    instagram_cookie_check_interval_minutes: int = int(
        os.getenv("INSTAGRAM_COOKIE_CHECK_INTERVAL_MINUTES", "180")
    )
    instagram_cookie_alert_cooldown_hours: int = int(
        os.getenv("INSTAGRAM_COOKIE_ALERT_COOLDOWN_HOURS", "12")
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
    admin_api_token: str = os.getenv("ADMIN_API_TOKEN", admin_api_key)
    admin_alert_email: str = os.getenv("ADMIN_ALERT_EMAIL", "yhadrami2003@gmail.com")
    admin_panel_url: str = os.getenv("ADMIN_PANEL_URL", "")
    smtp_host: str = os.getenv("SMTP_HOST", "")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_username: str = os.getenv("SMTP_USERNAME", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    smtp_from_email: str = os.getenv("SMTP_FROM_EMAIL", "")
    smtp_from_name: str = os.getenv("SMTP_FROM_NAME", "ApexLoad Backend")
    smtp_use_tls: bool = os.getenv("SMTP_USE_TLS", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    ytdlp_update_policy: str = os.getenv("YTDLP_UPDATE_POLICY", "manual")
    ffmpeg_location: str = os.getenv("FFMPEG_LOCATION", "")
    youtube_auth_mode: str = os.getenv("YOUTUBE_AUTH_MODE", "none").lower()
    youtube_cookie_file: str = os.getenv(
        "YOUTUBE_COOKIES_FILE",
        "secrets/youtube_cookies.txt",
    )
    # Backward-compatible alias for older deployments.
    enable_youtube_cookies: bool = (
        os.getenv("ENABLE_YOUTUBE_COOKIES", "false").lower()
        in {"1", "true", "yes", "on"}
    )
    youtube_cookies_file: str = youtube_cookie_file
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
