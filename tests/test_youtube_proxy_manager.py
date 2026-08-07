import os
import threading
import time

import pytest

from app.core.config import Settings
from app.services.youtube_error_classifier import (
    YouTubeErrorCode,
    classify_youtube_error,
)
from app.services.youtube_proxy_manager import (
    ProxyHealth,
    YouTubeProxyCapabilityUnavailableError,
    YouTubeProxyManager,
    normalize_proxy_entry,
    parse_proxy_list,
    youtube_video_resolutions,
)
from app.services.ytdlp_options import apply_anonymous_youtube_proxy


def proxy_settings() -> Settings:
    settings = Settings()
    settings.youtube_proxy_cache_ttl_seconds = 900
    settings.youtube_proxy_failure_cooldown_seconds = 600
    settings.youtube_proxy_max_consecutive_failures = 2
    settings.youtube_proxy_pool_target = 1
    settings.youtube_proxy_max_candidates = 20
    settings.youtube_proxy_health_concurrency = 4
    settings.youtube_proxy_validation_concurrency = 2
    settings.youtube_proxy_validation_timeout = 3
    settings.youtube_proxy_source_cache_ttl_seconds = 900
    settings.youtube_proxy_enabled = True
    settings.youtube_proxy_prewarm_enabled = True
    settings.youtube_proxy_prewarm_url = "https://www.youtube.com/shorts/prewarm"
    settings.youtube_proxy_startup_wait_seconds = 1
    settings.youtube_proxy_revalidate_ahead_seconds = 120
    settings.youtube_proxy_maintenance_interval_seconds = 10
    settings.youtube_proxy_refresh_backoff_seconds = 1
    settings.youtube_proxy_refresh_max_backoff_seconds = 2
    settings.youtube_proxy_shutdown_timeout_seconds = 2
    return settings


def test_proxy_parsing_normalizes_deduplicates_and_rejects_internal_addresses() -> None:
    text = """
    8.8.8.8:1080
    socks5://8.8.8.8:1080
    socks5h://1.1.1.1:9050
    127.0.0.1:1080
    10.0.0.1:1080
    172.16.0.1:1080
    192.168.1.2:1080
    169.254.1.1:1080
    localhost:1080
    8.8.8.8:70000
    [2001:4860:4860::8888]:1080
    """
    assert parse_proxy_list(text) == [
        "socks5://8.8.8.8:1080",
        "socks5://1.1.1.1:9050",
    ]
    assert normalize_proxy_entry("socks5://user:pass@8.8.8.8:1080") is None


def test_anonymous_proxy_options_strip_all_youtube_credentials() -> None:
    options = {
        "cookiefile": "/secrets/youtube.txt",
        "cookiesfrombrowser": ("chrome",),
        "http_headers": {"User-Agent": "yt-dlp"},
    }
    apply_anonymous_youtube_proxy(options, "socks5://8.8.8.8:1080")
    assert "cookiefile" not in options
    assert "cookiesfrombrowser" not in options
    assert options["proxy"] == "socks5://8.8.8.8:1080"


def test_health_score_ttl_and_cooldown() -> None:
    now = 1_000.0
    healthy = ProxyHealth(
        "socks5://8.8.8.8:1080",
        last_success=950,
        success_count=5,
        average_validation_latency=0.5,
    )
    slow = ProxyHealth(
        "socks5://1.1.1.1:1080",
        last_success=950,
        success_count=5,
        average_validation_latency=4.0,
    )
    assert healthy.is_healthy(now, 900)
    assert healthy.score(now) > slow.score(now)
    healthy.cooldown_until = now + 1
    assert not healthy.is_healthy(now, 900)
    healthy.cooldown_until = 0
    assert not healthy.is_healthy(now + 901, 900)


def test_failure_cooldown_and_success_reset() -> None:
    current = [100.0]
    manager = YouTubeProxyManager(proxy_settings(), clock=lambda: current[0])
    proxy = "socks5://8.8.8.8:1080"
    manager.report_success(proxy, 0.4)
    manager.report_failure(proxy, "connection refused")
    assert manager.snapshot()[0].cooldown_until == 0
    manager.report_failure(proxy, "connection refused")
    assert manager.snapshot()[0].cooldown_until == 700
    current[0] = 200
    manager.report_success(proxy, 0.2)
    record = manager.snapshot()[0]
    assert record.consecutive_failures == 0
    assert record.cooldown_until == 0


def test_failure_classification() -> None:
    assert classify_youtube_error("LOGIN_REQUIRED: Sign in to confirm you're not a bot").code == YouTubeErrorCode.ANTI_BOT
    unavailable = classify_youtube_error("ERROR: Video unavailable")
    assert unavailable.code == YouTubeErrorCode.CONTENT_UNAVAILABLE
    assert unavailable.verify_with_another_proxy
    assert classify_youtube_error("HTTP Error 429").code == YouTubeErrorCode.RATE_LIMITED
    assert classify_youtube_error("Requested format is not available").code == YouTubeErrorCode.FORMAT_UNAVAILABLE


def test_single_flight_discovery() -> None:
    settings = proxy_settings()
    proxy = "socks5://8.8.8.8:1080"
    validation_calls = 0
    validation_lock = threading.Lock()

    def validator(_proxy: str, _url: str):
        nonlocal validation_calls
        with validation_lock:
            validation_calls += 1
        time.sleep(0.1)
        return True, 0.1, "success"

    manager = YouTubeProxyManager(settings, validator=validator)
    manager._load_candidates = lambda: [proxy]
    manager._connectivity_check = lambda _proxy: (True, "203.0.113.2")
    barrier = threading.Barrier(3)
    results: list[str] = []

    def acquire() -> None:
        barrier.wait()
        results.append(manager.acquire("https://www.youtube.com/watch?v=test"))

    threads = [threading.Thread(target=acquire) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=2)

    assert results == [proxy, proxy]
    assert validation_calls == 1


def test_background_prewarm_is_non_blocking_fills_target_and_stops_cleanly() -> None:
    settings = proxy_settings()
    settings.youtube_proxy_pool_target = 2
    proxies = ["socks5://8.8.8.8:1080", "socks5://1.1.1.1:1080"]
    validation_calls: list[str] = []

    def validator(proxy: str, _url: str):
        validation_calls.append(proxy)
        return True, 0.01, "success"

    manager = YouTubeProxyManager(settings, validator=validator)
    manager._load_candidates = lambda: proxies
    manager._connectivity_check = lambda _proxy: (True, "203.0.113.2")

    started = time.monotonic()
    manager.start_background()
    startup_elapsed = time.monotonic() - started
    thread = manager._background_thread
    try:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if len([record for record in manager.snapshot() if record.last_success]) == 2:
                break
            time.sleep(0.01)
        assert startup_elapsed < 0.2
        assert manager.wait_until_ready(1)
        assert len([record for record in manager.snapshot() if record.last_success]) == 2
        assert set(validation_calls) == set(proxies)
    finally:
        manager.stop_background()

    assert thread is not None
    assert not thread.is_alive()


def test_maintenance_revalidates_before_cache_expiry() -> None:
    settings = proxy_settings()
    settings.youtube_proxy_cache_ttl_seconds = 100
    settings.youtube_proxy_revalidate_ahead_seconds = 20
    current = [100.0]
    manager = YouTubeProxyManager(settings, clock=lambda: current[0])
    manager.report_success("socks5://8.8.8.8:1080")
    current[0] = 181.0

    with manager._condition:
        assert manager._healthy_count_locked() == 1
        assert manager._maintenance_healthy_count_locked() == 0


def test_background_maintenance_wakes_when_pool_degrades() -> None:
    settings = proxy_settings()
    settings.youtube_proxy_pool_target = 1
    proxy = "socks5://8.8.8.8:1080"
    validation_calls = 0

    def validator(_proxy: str, _url: str):
        nonlocal validation_calls
        validation_calls += 1
        return True, 0.01, "success"

    manager = YouTubeProxyManager(settings, validator=validator)
    manager._load_candidates = lambda: [proxy]
    manager._connectivity_check = lambda _proxy: (True, "203.0.113.2")
    manager.start_background()
    try:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and validation_calls < 1:
            time.sleep(0.01)
        assert validation_calls == 1

        manager.report_failure(proxy, "connection refused")
        manager.report_failure(proxy, "connection refused")
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and validation_calls < 2:
            time.sleep(0.01)
        assert validation_calls >= 2
    finally:
        manager.stop_background()


def test_request_can_use_first_ready_proxy_while_prewarm_finishes() -> None:
    settings = proxy_settings()
    settings.youtube_proxy_pool_target = 2
    fast_proxy = "socks5://8.8.8.8:1080"
    slow_proxy = "socks5://1.1.1.1:1080"
    release_slow = threading.Event()
    acquired = threading.Event()
    result: list[str] = []

    def validator(proxy: str, _url: str):
        if proxy == slow_proxy:
            release_slow.wait(timeout=2)
        return True, 0.01, "success"

    manager = YouTubeProxyManager(settings, validator=validator)
    manager._load_candidates = lambda: [fast_proxy, slow_proxy]
    manager._connectivity_check = lambda _proxy: (True, "203.0.113.2")
    manager.start_background()
    requester = threading.Thread(
        target=lambda: (
            result.append(manager.acquire("https://www.youtube.com/watch?v=request")),
            acquired.set(),
        )
    )
    try:
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            with manager._condition:
                if manager._discovery_running:
                    break
            time.sleep(0.01)
        requester.start()
        assert acquired.wait(timeout=1)
        assert result == [fast_proxy]
        assert not release_slow.is_set()
    finally:
        release_slow.set()
        requester.join(timeout=2)
        manager.stop_background()


def test_video_resolutions_use_exact_orientation_independent_short_edge() -> None:
    resolutions = youtube_video_resolutions(
        {
            "formats": [
                {
                    "url": "https://media.invalid/portrait-1080",
                    "vcodec": "avc1",
                    "width": 1080,
                    "height": 1920,
                },
                {
                    "url": "https://media.invalid/landscape-1080",
                    "vcodec": "avc1",
                    "width": 1920,
                    "height": 1080,
                },
                {
                    "url": "https://media.invalid/not-1080",
                    "vcodec": "avc1",
                    "width": 608,
                    "height": 1080,
                },
                {
                    "url": "https://media.invalid/audio",
                    "vcodec": "none",
                    "width": 2160,
                    "height": 3840,
                },
            ]
        }
    )

    assert resolutions == {608, 1080}


def test_analyze_proven_capability_is_preferred_over_generic_proxy() -> None:
    proxy_a = "socks5://8.8.8.8:1080"
    proxy_b = "socks5://1.1.1.1:1080"
    url = "https://www.youtube.com/shorts/target"

    def unexpected_validation(_proxy: str, _url: str):
        pytest.fail("cached analyze capability should avoid another validation")

    manager = YouTubeProxyManager(
        proxy_settings(),
        capability_validator=unexpected_validation,
    )
    manager.report_success(proxy_a)
    manager.report_success(proxy_b)
    manager.record_capability(url, proxy_b, {480, 720, 1080})

    assert manager.acquire(url, required_resolution=1080) == proxy_b
    assert manager.cached_resolutions(url, proxy_b) == {480, 720, 1080}


def test_healthy_lower_quality_proxy_is_rejected_for_required_resolution() -> None:
    proxy_a = "socks5://8.8.8.8:1080"
    proxy_b = "socks5://1.1.1.1:1080"
    url = "https://www.youtube.com/watch?v=target"
    validation_calls: list[tuple[str, str]] = []

    def capability_validator(proxy: str, request_url: str):
        validation_calls.append((proxy, request_url))
        if proxy == proxy_a:
            return {480}, 0.01, "success"
        return {480, 720, 1080}, 0.01, "success"

    manager = YouTubeProxyManager(
        proxy_settings(),
        capability_validator=capability_validator,
    )
    manager.report_success(proxy_a)
    manager.report_success(proxy_b)
    manager._load_candidates = lambda: []

    assert manager.acquire(url, required_resolution=1080) == proxy_b
    assert manager.cached_resolutions(url, proxy_a) == {480}
    assert manager.cached_resolutions(url, proxy_b) == {480, 720, 1080}
    assert {call[0] for call in validation_calls} == {proxy_a, proxy_b}
    assert all(call[1] == url for call in validation_calls)


def test_targeted_discovery_checks_fresh_proxy_after_cached_capability_miss() -> None:
    proxy_a = "socks5://8.8.8.8:1080"
    proxy_b = "socks5://1.1.1.1:1080"
    url = "https://www.youtube.com/shorts/target"
    validation_calls: list[str] = []

    def capability_validator(proxy: str, _url: str):
        validation_calls.append(proxy)
        return {480, 720, 1080}, 0.01, "success"

    manager = YouTubeProxyManager(
        proxy_settings(),
        capability_validator=capability_validator,
    )
    manager.report_success(proxy_a)
    manager.record_capability(url, proxy_a, {480})
    manager._load_candidates = lambda: [proxy_a, proxy_b]
    manager._connectivity_check = lambda proxy: (proxy == proxy_b, "203.0.113.2")

    assert manager.acquire(url, required_resolution=1080) == proxy_b
    assert validation_calls == [proxy_b]


def test_capabilities_are_url_specific_and_expire_with_health_cache() -> None:
    current = [100.0]
    proxy = "socks5://8.8.8.8:1080"
    first_url = "https://www.youtube.com/watch?v=first"
    second_url = "https://www.youtube.com/watch?v=second"
    manager = YouTubeProxyManager(
        proxy_settings(),
        clock=lambda: current[0],
        capability_validator=lambda *_args: ({480}, 0.01, "success"),
    )
    manager.report_success(proxy)
    manager.record_capability(first_url, proxy, {1080})
    manager._load_candidates = lambda: []

    assert manager.acquire(first_url, required_resolution=1080) == proxy
    with pytest.raises(YouTubeProxyCapabilityUnavailableError):
        manager.acquire(second_url, required_resolution=1080)

    current[0] += manager.settings.youtube_proxy_cache_ttl_seconds + 1
    assert manager.cached_resolutions(first_url, proxy) is None


def test_capability_discovery_is_single_flight() -> None:
    settings = proxy_settings()
    proxy = "socks5://8.8.8.8:1080"
    validation_calls = 0
    validation_lock = threading.Lock()

    def capability_validator(_proxy: str, _url: str):
        nonlocal validation_calls
        with validation_lock:
            validation_calls += 1
        time.sleep(0.1)
        return {1080}, 0.1, "success"

    manager = YouTubeProxyManager(
        settings,
        capability_validator=capability_validator,
    )
    manager.report_success(proxy)
    manager._load_candidates = lambda: []
    barrier = threading.Barrier(3)
    results: list[str] = []

    def acquire() -> None:
        barrier.wait()
        results.append(
            manager.acquire(
                "https://www.youtube.com/watch?v=single-flight",
                required_resolution=1080,
            )
        )

    threads = [threading.Thread(target=acquire) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=2)

    assert results == [proxy, proxy]
    assert validation_calls == 1


@pytest.mark.skipif(
    os.getenv("RUN_YOUTUBE_PROXY_INTEGRATION") != "1",
    reason="manual public-proxy integration test",
)
def test_manual_real_proxy_discovery() -> None:
    manager = YouTubeProxyManager(proxy_settings())
    proxy = manager.acquire("https://www.youtube.com/shorts/SybaN0KNRhY")
    assert proxy.startswith("socks5://")
