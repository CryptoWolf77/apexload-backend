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
    instagram_safety_state_path: str = os.getenv("INSTAGRAM_SAFETY_STATE_PATH", "")
    instagram_safety_mode_enabled: bool = (
        os.getenv("INSTAGRAM_SAFETY_MODE_ENABLED", "true").lower()
        in {"1", "true", "yes", "on"}
    )
    instagram_max_concurrent_jobs: int = int(os.getenv("INSTAGRAM_MAX_CONCURRENT_JOBS", "1"))
    instagram_max_requests_per_minute: int = int(
        os.getenv("INSTAGRAM_MAX_REQUESTS_PER_MINUTE", "3")
    )
    instagram_max_requests_per_hour: int = int(
        os.getenv("INSTAGRAM_MAX_REQUESTS_PER_HOUR", "60")
    )
    instagram_failure_threshold: int = int(os.getenv("INSTAGRAM_FAILURE_THRESHOLD", "3"))
    instagram_restriction_cooldown_hours: int = int(
        os.getenv("INSTAGRAM_RESTRICTION_COOLDOWN_HOURS", "72")
    )
    instagram_rate_limit_cooldown_hours: int = int(
        os.getenv("INSTAGRAM_RATE_LIMIT_COOLDOWN_HOURS", "24")
    )
    instagram_unknown_error_cooldown_minutes: int = int(
        os.getenv("INSTAGRAM_UNKNOWN_ERROR_COOLDOWN_MINUTES", "30")
    )
    instagram_recovery_success_threshold: int = int(
        os.getenv("INSTAGRAM_RECOVERY_SUCCESS_THRESHOLD", "2")
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
    smtp_use_ssl: bool = os.getenv("SMTP_USE_SSL", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    legal_notification_email: str = os.getenv(
        "LEGAL_NOTIFICATION_EMAIL",
        "copyright@apexload.org",
    )
    legal_from_email: str = os.getenv(
        "LEGAL_FROM_EMAIL",
        "ApexLoad Legal <legal@apexload.org>",
    )
    legal_allowed_origins: list[str] = [
        origin.strip()
        for origin in os.getenv(
            "LEGAL_ALLOWED_ORIGINS",
            "https://apexload.org,https://www.apexload.org",
        ).split(",")
        if origin.strip()
    ]
    legal_trusted_proxy_cidrs: list[str] = [
        cidr.strip()
        for cidr in os.getenv("LEGAL_TRUSTED_PROXY_CIDRS", "").split(",")
        if cidr.strip()
    ]
    legal_max_request_bytes: int = int(os.getenv("LEGAL_MAX_REQUEST_BYTES", "65536"))
    # The longer names are the public deployment contract. Keep the earlier
    # names as read-only compatibility aliases for existing Coolify installs.
    legal_rate_limit_hour: int = int(
        os.getenv("LEGAL_RATE_LIMIT_HOURLY", os.getenv("LEGAL_RATE_LIMIT_HOUR", "5"))
    )
    legal_rate_limit_day: int = int(
        os.getenv("LEGAL_RATE_LIMIT_DAILY", os.getenv("LEGAL_RATE_LIMIT_DAY", "15"))
    )
    legal_duplicate_window_seconds: int = int(
        os.getenv("LEGAL_DUPLICATE_WINDOW_SECONDS", "86400")
    )
    legal_pending_window_seconds: int = int(
        os.getenv("LEGAL_PENDING_WINDOW_SECONDS", "300")
    )
    legal_min_form_seconds: int = int(os.getenv("LEGAL_MIN_FORM_SECONDS", "3"))
    legal_clock_skew_seconds: int = int(os.getenv("LEGAL_CLOCK_SKEW_SECONDS", "300"))
    legal_email_timeout_seconds: int = int(
        os.getenv("LEGAL_EMAIL_TIMEOUT_SECONDS", "20")
    )
    legal_fingerprint_secret: str = os.getenv("LEGAL_FINGERPRINT_SECRET", "")
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
