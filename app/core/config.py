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
    youtube_proxy_enabled: bool = os.getenv("YOUTUBE_PROXY_ENABLED", "true").lower() in {
        "1", "true", "yes", "on"
    }
    youtube_proxy_direct_first: bool = os.getenv(
        "YOUTUBE_PROXY_DIRECT_FIRST", "false"
    ).lower() in {"1", "true", "yes", "on"}
    youtube_proxy_max_job_attempts: int = max(
        1, int(os.getenv("YOUTUBE_PROXY_MAX_JOB_ATTEMPTS", "4"))
    )
    youtube_proxy_health_timeout: float = max(
        1.0, float(os.getenv("YOUTUBE_PROXY_HEALTH_TIMEOUT", "5"))
    )
    youtube_proxy_validation_timeout: float = max(
        3.0, float(os.getenv("YOUTUBE_PROXY_VALIDATION_TIMEOUT", "15"))
    )
    youtube_proxy_health_concurrency: int = min(
        40, max(1, int(os.getenv("YOUTUBE_PROXY_HEALTH_CONCURRENCY", "30")))
    )
    youtube_proxy_validation_concurrency: int = min(
        5, max(1, int(os.getenv("YOUTUBE_PROXY_VALIDATION_CONCURRENCY", "3")))
    )
    youtube_proxy_cache_ttl_seconds: int = max(
        60, int(os.getenv("YOUTUBE_PROXY_CACHE_TTL_SECONDS", "900"))
    )
    youtube_proxy_failure_cooldown_seconds: int = max(
        30, int(os.getenv("YOUTUBE_PROXY_FAILURE_COOLDOWN_SECONDS", "600"))
    )
    youtube_proxy_max_consecutive_failures: int = max(
        1, int(os.getenv("YOUTUBE_PROXY_MAX_CONSECUTIVE_FAILURES", "2"))
    )
    youtube_proxy_max_candidates: int = min(
        500, max(1, int(os.getenv("YOUTUBE_PROXY_MAX_CANDIDATES", "200")))
    )
    youtube_proxy_pool_target: int = min(
        10, max(1, int(os.getenv("YOUTUBE_PROXY_POOL_TARGET", "4")))
    )
    youtube_proxy_prewarm_enabled: bool = os.getenv(
        "YOUTUBE_PROXY_PREWARM_ENABLED", "true"
    ).lower() in {"1", "true", "yes", "on"}
    youtube_proxy_prewarm_url: str = os.getenv(
        "YOUTUBE_PROXY_PREWARM_URL",
        "https://www.youtube.com/shorts/SybaN0KNRhY",
    ).strip()
    youtube_proxy_startup_wait_seconds: float = max(
        0.0, float(os.getenv("YOUTUBE_PROXY_STARTUP_WAIT_SECONDS", "30"))
    )
    youtube_proxy_revalidate_ahead_seconds: int = min(
        max(0, youtube_proxy_cache_ttl_seconds - 1),
        max(0, int(os.getenv("YOUTUBE_PROXY_REVALIDATE_AHEAD_SECONDS", "120"))),
    )
    youtube_proxy_maintenance_interval_seconds: int = max(
        30, int(os.getenv("YOUTUBE_PROXY_MAINTENANCE_INTERVAL_SECONDS", "120"))
    )
    youtube_proxy_refresh_backoff_seconds: int = max(
        30, int(os.getenv("YOUTUBE_PROXY_REFRESH_BACKOFF_SECONDS", "60"))
    )
    youtube_proxy_refresh_max_backoff_seconds: int = max(
        youtube_proxy_refresh_backoff_seconds,
        int(os.getenv("YOUTUBE_PROXY_REFRESH_MAX_BACKOFF_SECONDS", "900")),
    )
    youtube_proxy_shutdown_timeout_seconds: float = max(
        1.0, float(os.getenv("YOUTUBE_PROXY_SHUTDOWN_TIMEOUT_SECONDS", "25"))
    )
    youtube_proxy_source_cache_ttl_seconds: int = max(
        60, int(os.getenv("YOUTUBE_PROXY_SOURCE_CACHE_TTL_SECONDS", "900"))
    )
    youtube_player_client: str = os.getenv("YOUTUBE_PLAYER_CLIENT", "mweb").strip() or "mweb"
    youtube_proxy_connectivity_url: str = os.getenv(
        "YOUTUBE_PROXY_CONNECTIVITY_URL", "http://api.iplocate.io/ip"
    )
    youtube_proxy_source_urls: list[str] = [
        value.strip()
        for value in os.getenv(
            "YOUTUBE_PROXY_SOURCE_URLS",
            (
                "https://raw.githubusercontent.com/iplocate/free-proxy-list/"
                "main/protocols/socks5.txt,"
                "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/"
                "proxies/protocols/socks5/data.txt"
            ),
        ).split(",")
        if value.strip()
    ]
    youtube_proxy_source_allowed_hosts: list[str] = [
        value.strip().lower()
        for value in os.getenv(
            "YOUTUBE_PROXY_SOURCE_ALLOWED_HOSTS",
            "raw.githubusercontent.com,cdn.jsdelivr.net",
        ).split(",")
        if value.strip()
    ]
    bgutil_base_url: str = os.getenv(
        "BGUTIL_BASE_URL", "http://127.0.0.1:4416"
    ).rstrip("/")
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
